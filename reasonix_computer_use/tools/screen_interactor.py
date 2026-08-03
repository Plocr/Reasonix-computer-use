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

import os
from typing import Any, Dict, List, Optional

from ..protocol import (
    CoordinateConverter,
    ScreenSnapshot, ActionCommand,
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


def _suggest_action(name: str) -> str:
    """Return the closest valid action name for a misspelled one, or ''.

    Host agents occasionally invent action names (e.g. ``dblClick`` instead
    of ``double_click``).  A concrete suggestion in the error message lets
    them self-correct instead of guessing again.
    """
    import difflib

    lowered = name.casefold().strip()
    if not lowered:
        return ""
    aliases = {candidate.casefold(): candidate for candidate in ALL_ACTIONS}
    matches = difflib.get_close_matches(lowered, list(aliases), n=1, cutoff=0.7)
    return aliases[matches[0]] if matches else ""


def _resolve_target(
    action: ActionCommand,
    snapshot: ScreenSnapshot,
    converter: CoordinateConverter,
    platform: PlatformProvider,
) -> tuple[int, int, Optional[tuple[int, int, int, int]]]:
    """Resolve an action's target to physical screen coordinates.

    Returns (x, y, window_rect): the physical pixel target and the window
    rect that normalized coordinates were mapped against (None when the
    target came from an element ref or the raw cursor).  The rect is
    echoed back in the tool response so the host can verify how a
    normalized coordinate was interpreted.

    Priority: ELEMENT_REF → fallback normalized coord → foreground window
    center → (0, 0) (never raises for a missing target).
    """
    # 1. Try element ref against latest snapshot.
    #    A stale/unknown ref must NOT silently fall back to the window
    #    center — that is how a click lands on the wrong control after the
    #    UI changed (observed in QQ Music: e45 resolved to (780,524) instead
    #    of the search box).  Surface the staleness to the host instead.
    if action.element_ref:
        el = snapshot.find_element(action.element_ref)
        if el is None:
            raise ValueError(
                f"element_ref {action.element_ref!r} no longer exists in the "
                "latest snapshot (UI changed); call observe() again to get "
                "fresh element IDs")
        if el.bbox[2] <= el.bbox[0] or el.bbox[3] <= el.bbox[1]:
            raise ValueError(
                f"element_ref '{action.element_ref}' has an empty bounding box")
        cx = (el.bbox[0] + el.bbox[2]) // 2
        cy = (el.bbox[1] + el.bbox[3]) // 2
        return (cx, cy, None)

    # 2. Try fallback normalized coordinate.
    #    IMPORTANT: with a foreground window present, CLAUDE_1024 and
    #    GEMINI_1000 map to the WINDOW INTERIOR (window-relative), not the
    #    full display.  PIXEL space is used verbatim.  The rect used is
    #    returned so the mapping is transparent to the host.
    if action.fallback:
        fg = platform.get_foreground_window()
        window_rect = None
        if fg is not None and fg.rect[2] > fg.rect[0] and fg.rect[3] > fg.rect[1]:
            window_rect = fg.rect
        x, y = converter.to_physical(action.fallback, window_rect=window_rect)
        # The rect echoed back is the one actually used (None = full display),
        # so the host's 物理x - rect[0] verification stays truthful.
        return (x, y, window_rect)

    # 3. Ultimate fallback: center of foreground window (cross-platform safe)
    fg = platform.get_foreground_window()
    if fg and fg.rect[2] > fg.rect[0]:
        cx = (fg.rect[0] + fg.rect[2]) // 2
        cy = (fg.rect[1] + fg.rect[3]) // 2
        return (cx, cy, fg.rect)
    # Last resort: origin — action will land at (0,0) rather than crash
    return (0, 0, None)


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

        # Get annotated image path from EasyOCR only when vision was used.
        # Precision snapshots must not carry a stale screenshot from an
        # earlier vision observation.
        self._annotated_image = None
        if snapshot.source == "vision":
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
            "screenshot_path": snapshot.screenshot_path,
            "quality_hint": snapshot.quality_hint,
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
                suggestion = _suggest_action(action_type)
                hint = f" Did you mean '{suggestion}'?" if suggestion else ""
                return {"status": "error", "code": "unknown_action_type",
                        "message": f"Unknown action type: {action_type}.{hint}"
                                   f" Valid types: {', '.join(sorted(ALL_ACTIONS))}"}
            parsed.append(ActionCommand.from_dict(act))

        # Refresh converter
        self._converter = CoordinateConverter.from_system_index()

        # Staleness detection: if the host claims a snapshot revision, refuse
        # to act on an older snapshot.  The host must re-observe first.
        if revision is not None:
            try:
                expected = int(revision)
            except (TypeError, ValueError):
                return {"status": "error", "code": "invalid_revision",
                        "message": f"revision must be an integer, got {revision!r}"}
            if self._latest_snapshot is None or self._revision != expected:
                return {"status": "error", "code": "stale_revision",
                        "message": (f"snapshot revision {self._revision} does not match "
                                     f"expected {expected}; call observe() again before executing")}

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
            snap.revision = self._revision
            self._latest_snapshot = snap  # keep revision ↔ snapshot consistent
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
            x, y, window_rect = _resolve_target(
                cmd, self._latest_snapshot or ScreenSnapshot(0, "", "unknown"),
                self._converter, self._platform,
            )
            result["x"] = x
            result["y"] = y
            if window_rect is not None:
                result["window_rect"] = list(window_rect)

            if cmd.type in ("click", "click_ref", "click_point", "click_text"):
                self._platform.mouse_click(x, y, button="left", count=1)
            elif cmd.type == "double_click":
                self._platform.mouse_click(x, y, button="left", count=2)
                # Give the app time to react to the double click (open a
                # detail panel, start playback, ...) before the host
                # re-observes; prevents a premature follow-up action.
                import time
                time.sleep(0.3)
            elif cmd.type == "right_click":
                self._platform.mouse_click(x, y, button="right", count=1)
            elif cmd.type == "middle_click":
                self._platform.mouse_click(x, y, button="middle", count=1)
            elif cmd.type == "hover" or cmd.type == "move":
                self._platform.mouse_move(x, y)
            elif cmd.type == "drag":
                # Drag: element_ref/fallback resolves the start point;
                # to_x/to_y (physical pixels) resolve the destination.
                to_x = x + 50
                to_y = y + 50  # default small drag
                if cmd.to_x is not None:
                    to_x = int(cmd.to_x)
                if cmd.to_y is not None:
                    to_y = int(cmd.to_y)
                self._platform.mouse_drag(x, y, to_x, to_y, duration=cmd.duration or 0.3)
                result["drag_to"] = [to_x, to_y]
            return result

        # ── Keyboard actions ──────────────────────────────────────────
        if cmd.type in ("type",):
            if not cmd.text:
                return {"type": cmd.type, "status": "error",
                        "code": "empty_text",
                        "error": "type action requires non-empty text (use 'text', "
                                 "'keyboard' or 'value' field)"}
            # Cross-process replay guard: the same injection signature
            # within the TTL window is blocked before any keystroke is
            # sent (prevents a crashed/restarted task from re-injecting
            # the same text blindly).
            try:
                from ..input_guard import reserve_text_input
                from ..platform.windows import WindowsPlatformProvider
                fg = self._platform.get_foreground_window()
                window_class = ""
                if fg is not None and isinstance(self._platform, WindowsPlatformProvider):
                    window_class = WindowsPlatformProvider._window_class(int(fg.id))
                # Persistent state hash: window identity (pid+title) plus
                # revision and batch position.  The pid/title part survives
                # process restarts so a crashed task cannot replay the same
                # injection after the MCP server restarts; the batch index
                # prevents two identical fields in one batch from being
                # mistaken for each other.
                app_identity = (fg.process_name if fg and fg.process_name
                                else (fg.title if fg else ""))
                state_hash = (f"{fg.process_id}:{fg.title}:rev{self._revision}"
                              if fg else f"rev{self._revision}")
                reserved = reserve_text_input(
                    app_identity=app_identity,
                    window_class=window_class,
                    state_hash=state_hash,
                    target_ref=str(cmd.element_ref or ""),
                    text=cmd.text)
                if reserved is False:  # only a true replay blocks
                    result = {"type": cmd.type, "status": "error",
                              "code": "input_replay_blocked",
                              "error": "检测到同一文本输入已被注入过；已阻止重放"}
                    return result
                # None (guard infra failure) fails open by design.
            except Exception:
                # Guard is best-effort; never block input on guard failure.
                pass
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
            fd, path = tempfile.mkstemp(suffix=".png", prefix="screen_")
            os.close(fd)
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
        """Post-execution verification.

        Supported checks:
          text_present — str | list[str]  — every text must be found
          text_absent  — str | list[str]  — every text must NOT be found
          contains     — bool             — substring match instead of exact
        """
        verification: dict = {"checked": []}
        all_passed = True
        contains = bool(expect.get("contains"))

        try:
            snap = self._router.observe(max_elements=40)
            texts = [el.text for el in snap.elements if el.text]
        except Exception:
            verification["error"] = "verification observe failed"
            verification["passed"] = False
            return verification

        def _match(expected: str, actuals: List[str]) -> bool:
            folded = expected.casefold()
            if contains:
                return any(folded in actual.casefold() for actual in actuals)
            return folded in {actual.casefold() for actual in actuals}

        for kind in ("text_present", "text_absent"):
            targets = expect.get(kind)
            if not targets:
                continue
            if isinstance(targets, str):
                targets = [targets]
            for t in targets:
                found = _match(t, texts)
                if kind == "text_absent":
                    found = not found
                verification["checked"].append({kind: t, "found": found})
                if not found:
                    all_passed = False

        verification["passed"] = all_passed
        return verification
