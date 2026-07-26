"""
screen_interactor — the core Computer Use execution tool.

Unifies observation (previously computer_state) and action execution
(previously computer_action) behind a single tool with two modes:

  observe   — Return a structured ScreenSnapshot via the perception router.
              The host Agent uses ELEMENT_REFs from the snapshot to issue actions.

  execute   — Run a batch of ActionCommands (up to 5) against the latest
              snapshot revision.  Commands carry ELEMENT_REF (preferred) with
              a normalized-coordinate fallback.

All coordinates use the normalized protocol (CLAUDE_1024, GEMINI_1000, PIXEL,
ELEMENT_REF).  Conversion to physical pixels happens internally.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..protocol import (
    CoordinateSpace, CoordinateConverter, NormalizedCoord,
    ScreenSnapshot, ActionCommand, ElementRef,
)
from ..perception import PerceptionRouter
from ..platform import get_platform, PlatformProvider
from ..services import get_profiler


# ── Action type registry ────────────────────────────────────────────────────

# Actions that require a target (element ref or coordinate)
POINT_ACTIONS = {
    "click", "click_ref", "click_point", "click_text",
    "double_click", "right_click", "middle_click",
    "hover", "move", "drag",
}

# Actions that modify the keyboard
KEY_ACTIONS = {"type", "press", "press_key", "key", "key_down", "key_up", "enter", "submit"}

# Actions that scroll
SCROLL_ACTIONS = {"scroll"}

# All recognized action types
ALL_ACTIONS = POINT_ACTIONS | KEY_ACTIONS | SCROLL_ACTIONS | {
    "wait", "save_as", "screenshot", "submit", "select_cell", "select_range",
}


def _resolve_target(
    action: ActionCommand,
    snapshot: ScreenSnapshot,
    converter: CoordinateConverter,
    platform: PlatformProvider,
) -> tuple[int, int]:
    """Resolve an action's target to physical screen coordinates.

    Priority: ELEMENT_REF → fallback normalized coord → current cursor.
    Never returns None — falls back to current cursor position.
    """
    # 1. Try element ref against latest snapshot
    if action.element_ref:
        el = snapshot.find_element(action.element_ref)
        if el and el.bbox[2] > el.bbox[0]:
            cx = (el.bbox[0] + el.bbox[2]) // 2
            cy = (el.bbox[1] + el.bbox[3]) // 2
            return (cx, cy)

    # 2. Try fallback normalized coordinate
    if action.fallback:
        fg = platform.get_foreground_window()
        window_rect = fg.rect if fg else None
        return converter.to_physical(action.fallback, window_rect=window_rect)

    # 3. Ultimate fallback: current cursor position (don't crash)
    import ctypes
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return (int(pt.x), int(pt.y))


# ── screen_interactor tool ──────────────────────────────────────────────────

class ScreenInteractor:
    """Core tool: observe screens and execute actions.

    This is the primary Computer Use tool exposed to the host Agent.
    """

    def __init__(self):
        self._router = PerceptionRouter()
        self._platform = get_platform()
        self._converter = CoordinateConverter.from_system_index()
        self._latest_snapshot: Optional[ScreenSnapshot] = None
        self._revision: int = 0
        self._was_blocked: bool = False  # auto-reset on next observe
        self._annotated_image: Optional[str] = None  # path to last annotated vision screenshot

    # ── Observe ──────────────────────────────────────────────────────────

    async def observe(
        self,
        window_id: Optional[str] = None,
        max_elements: int = 80,
        force_vision: bool = False,
    ) -> dict:
        """Observe the given window and return a structured snapshot.

        Returns JSON-serializable dict with elements, revision, window info.
        Auto-resets blocked state.
        """
        self._revision += 1
        self._was_blocked = False  # Reset blocked state on fresh observe

        snapshot = self._router.observe(
            window_id=window_id,
            max_elements=max_elements,
            force_vision=force_vision,
        )

        # Enrich with scale_factor from system index
        profiler = get_profiler()
        snapshot.scale_factor = profiler.get_scale_factor()

        # Refresh converter from system index
        self._converter = CoordinateConverter.from_system_index()
        self._latest_snapshot = snapshot
        snapshot.revision = self._revision

        # Get annotated image path from EasyOCR if available
        try:
            from ..perception.vision.easy_ocr import _last_annotated
            self._annotated_image = _last_annotated
        except ImportError:
            pass

        return {
            "status": "ok",
            "revision": self._revision,
            "window_id": snapshot.window_id,
            "source": snapshot.source,
            "elements": [el.to_dict() for el in snapshot.elements],
            "width": snapshot.width,
            "height": snapshot.height,
            "scale_factor": snapshot.scale_factor,
            "input_ready": snapshot.input_ready,
            "blocked": snapshot.blocked,
            "blocked_reason": snapshot.blocked_reason if snapshot.blocked else "",
            "annotated_image": self._annotated_image or "",
        }

    # ── Execute ──────────────────────────────────────────────────────────

    async def execute(
        self,
        actions: List[Dict[str, Any]],
        revision: Optional[int] = None,
        expect: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Execute a batch of up to 5 ActionCommands.

        Args:
            actions: List of action dicts, each with type, element_ref, fallback, etc.
            revision: Expected snapshot revision (for staleness detection).
            expect: Optional post-execution verification.

        Returns:
            Result dict with status, verification, and any blocked state.
        """
        if not actions:
            return {"status": "error", "code": "no_actions", "message": "actions list is empty"}

        if len(actions) > 5:
            return {"status": "error", "code": "too_many_actions",
                    "message": f"Maximum 5 actions per batch, got {len(actions)}"}

        # Parse actions
        parsed: List[ActionCommand] = []
        for act in actions:
            if not isinstance(act, dict):
                return {"status": "error", "code": "invalid_action",
                        "message": f"Each action must be a dict, got {type(act).__name__}"}
            action_type = act.get("type", "")
            if action_type not in ALL_ACTIONS:
                return {"status": "error", "code": "unknown_action_type",
                        "message": f"Unknown action type: {action_type}"}
            parsed.append(ActionCommand.from_dict(act))

        # Refresh converter
        self._converter = CoordinateConverter.from_system_index()

        results: List[dict] = []
        blocked = False
        blocked_reason = ""

        for i, cmd in enumerate(parsed):
            try:
                result = self._execute_one(cmd)
                results.append(result)
                if result.get("blocked"):
                    blocked = True
                    blocked_reason = result.get("blocked_reason", "action blocked")
                    self._was_blocked = True  # Mark so route_guard can see
                    break
            except Exception as e:
                results.append({
                    "index": i, "type": cmd.type, "status": "error",
                    "error": str(e),
                })

        # Wrap up result
        response: dict = {
            "status": "ok" if not blocked else "blocked",
            "actions_executed": len(results),
            "results": results,
        }

        # Auto-append lightweight observe snapshot after execute
        import logging
        try:
            self._revision += 1
            snap = self._router.observe(max_elements=30)
            response["after"] = {
                "revision": self._revision,
                "window_id": snap.window_id,
                "source": snap.source,
                "element_count": len(snap.elements),
                "blocked": snap.blocked,
                "blocked_reason": snap.blocked_reason if snap.blocked else "",
            }
        except Exception as exc:
            logging.getLogger(__name__).warning("post-execute observe failed: %s", exc)

        if blocked:
            response["blocked"] = True
            response["blocked_reason"] = blocked_reason

        # Post-execution verification
        if expect:
            verification = self._verify(expect)
            response["verification"] = verification

        return response

    def _execute_one(self, cmd: ActionCommand) -> dict:
        """Execute a single action command."""
        result: dict = {"type": cmd.type, "status": "ok"}

        # ── Wait ──────────────────────────────────────────────────────
        if cmd.type == "wait":
            import time
            duration = cmd.duration or 0.5
            time.sleep(max(0.05, min(duration, 10.0)))
            result["duration"] = duration
            return result

        # ── Point actions ─────────────────────────────────────────────
        if cmd.type in POINT_ACTIONS:
            target = _resolve_target(
                cmd, self._latest_snapshot or ScreenSnapshot(0, "", "unknown"),
                self._converter, self._platform,
            )
            x, y = target
            result["x"] = x
            result["y"] = y

            if cmd.type in ("click", "click_ref", "click_point", "click_text"):
                self._platform.mouse_click(x, y, button="left", count=1)
            elif cmd.type == "double_click":
                self._platform.mouse_click(x, y, button="left", count=2)
            elif cmd.type == "right_click":
                self._platform.mouse_click(x, y, button="right", count=1)
            elif cmd.type == "middle_click":
                self._platform.mouse_click(x, y, button="middle", count=1)
            elif cmd.type == "hover" or cmd.type == "move":
                self._platform.mouse_move(x, y)
            elif cmd.type == "drag":
                # Drag: use element_ref for start, extra keys/amount for delta
                to_x = x + 50
                to_y = y + 50  # default small drag
                raw = cmd.to_dict() if hasattr(cmd, 'to_dict') else {}
                if 'to_x' in raw:
                    to_x = int(raw['to_x'])
                if 'to_y' in raw:
                    to_y = int(raw['to_y'])
                self._platform.mouse_drag(x, y, to_x, to_y, duration=cmd.duration or 0.3)
                result["drag_to"] = [to_x, to_y]
            return result

        # ── Keyboard actions ──────────────────────────────────────────
        if cmd.type in ("type",):
            if cmd.text:
                self._platform.keyboard_type(cmd.text)
                result["text_length"] = len(cmd.text)
            return result

        if cmd.type in ("press", "press_key", "key", "submit", "enter"):
            keys_to_press = cmd.keys
            # Also check 'key' field (singular) as fallback
            if not keys_to_press and cmd.text:
                keys_to_press = [cmd.text]
            if keys_to_press:
                self._platform.keyboard_press(keys_to_press)
                result["keys"] = keys_to_press
            else:
                self._platform.keyboard_press(["enter"])
                result["keys"] = ["enter"]
            return result

        if cmd.type == "key_down":
            if cmd.text:
                self._platform.keyboard_key_down(cmd.text)
            return result

        if cmd.type == "key_up":
            if cmd.text:
                self._platform.keyboard_key_up(cmd.text)
            return result

        # ── Scroll ────────────────────────────────────────────────────
        if cmd.type == "scroll":
            # Resolve target, fallback to cursor position
            target = _resolve_target(
                cmd, self._latest_snapshot or ScreenSnapshot(0, "", "unknown"),
                self._converter, self._platform,
            )
            x, y = int(target[0]), int(target[1])
            self._platform.mouse_scroll(x, y, cmd.amount or 3)
            result["amount"] = cmd.amount
            return result

        # ── Screenshot ────────────────────────────────────────────────
        if cmd.type == "screenshot":
            import tempfile
            img = self._platform.screenshot()
            path = tempfile.mktemp(suffix=".png", prefix="screen_")
            img.save(path)
            result["path"] = path
            return result

        # ── Save as ───────────────────────────────────────────────────
        if cmd.type == "save_as":
            result["note"] = "save_as not yet implemented for screen_interactor"
            return result

        return {"type": cmd.type, "status": "error", "error": f"Unhandled action: {cmd.type}"}

    # ── Verification ─────────────────────────────────────────────────────

    def _verify(self, expect: Dict[str, Any]) -> dict:
        """Post-execution verification."""
        verification: dict = {"checked": []}
        all_passed = True

        if expect.get("text_present"):
            # Re-observe and check for text
            try:
                snap = self._router.observe(max_elements=40)
                texts = {el.text.casefold() for el in snap.elements if el.text}
                for t in ([expect["text_present"]] if isinstance(expect["text_present"], str)
                          else expect["text_present"]):
                    found = t.casefold() in texts
                    verification["checked"].append({"text": t, "found": found})
                    if not found:
                        all_passed = False
            except Exception:
                all_passed = False
                verification["error"] = "verification observe failed"

        verification["passed"] = all_passed
        return verification
