"""
macOS PlatformProvider — implementation via PyObjC (CoreGraphics / AppKit).

All coordinates accepted by public methods are PHYSICAL screen pixels.
macOS global coordinates are logical points; this provider converts with
the main display's backing scale factor (Retina = 2.0), so callers keep
the same pixel semantics as Windows/Linux.

Input injection requires the Accessibility permission (AXIsProcessTrusted);
methods raise a clear OSError with guidance when it is missing instead of
silently failing.  Screen capture requires the Screen Recording permission.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .base import PlatformProvider, WindowInfo
from .common import (
    CLICK_GAP,
    FfmpegRecorder,
    MAX_DURATION,
    MOVE_SETTLE,
    PRESS_HOLD,
    SCROLL_SETTLE,
    clamp_count,
    drag_plan,
    normalize_key_name,
)

# CGEvent mouse button mapping: 0=left, 1=right, 2=center
_BUTTON_NUMBERS = {"left": 0, "right": 1, "middle": 2}
_MOUSE_DOWN_EVENTS = {0: "kCGEventLeftMouseDown", 1: "kCGEventRightMouseDown",
                      2: "kCGEventOtherMouseDown"}
_MOUSE_UP_EVENTS = {0: "kCGEventLeftMouseUp", 1: "kCGEventRightMouseUp",
                    2: "kCGEventOtherMouseUp"}


def _mac_keycodes() -> dict:
    """Key name -> macOS virtual keycode table (kVK_* from Quartz)."""
    import Quartz
    return {
        "enter": Quartz.kVK_Return, "return": Quartz.kVK_Return,
        "tab": Quartz.kVK_Tab, "backspace": Quartz.kVK_Delete,
        "delete": Quartz.kVK_ForwardDelete, "escape": Quartz.kVK_Escape,
        "esc": Quartz.kVK_Escape, "space": Quartz.kVK_Space,
        "up": Quartz.kVK_UpArrow, "down": Quartz.kVK_DownArrow,
        "left": Quartz.kVK_LeftArrow, "right": Quartz.kVK_RightArrow,
        "home": Quartz.kVK_Home, "end": Quartz.kVK_End,
        "pageup": Quartz.kVK_PageUp, "pagedown": Quartz.kVK_PageDown,
        "insert": Quartz.kVK_Help, "pause": Quartz.kVK_F15,
        "capslock": Quartz.kVK_CapsLock, "printscreen": Quartz.kVK_F13,
        "prtsc": Quartz.kVK_F13, "apps": Quartz.kVK_F16,
        "numlock": Quartz.kVK_Clear, "scrolllock": Quartz.kVK_F14,
        "shift": Quartz.kVK_Shift, "ctrl": Quartz.kVK_Control,
        "alt": Quartz.kVK_Option, "super": Quartz.kVK_Command,
        "meta": Quartz.kVK_Command, "option": Quartz.kVK_Option,
        "f1": Quartz.kVK_F1, "f2": Quartz.kVK_F2, "f3": Quartz.kVK_F3,
        "f4": Quartz.kVK_F4, "f5": Quartz.kVK_F5, "f6": Quartz.kVK_F6,
        "f7": Quartz.kVK_F7, "f8": Quartz.kVK_F8, "f9": Quartz.kVK_F9,
        "f10": Quartz.kVK_F10, "f11": Quartz.kVK_F11, "f12": Quartz.kVK_F12,
        "volume_mute": Quartz.kVK_Mute, "volume_down": Quartz.kVK_VolumeDown,
        "volume_up": Quartz.kVK_VolumeUp, "media_next": Quartz.kVK_Next,
        "media_prev": Quartz.kVK_Previous, "media_stop": Quartz.kVK_Play,
        "media_play_pause": Quartz.kVK_Play,
    }


def _modifier_flags() -> dict:
    """Key name -> CGEvent modifier flag mask."""
    import Quartz
    return {
        "shift": Quartz.kCGEventFlagMaskShift,
        "ctrl": Quartz.kCGEventFlagMaskControl,
        "alt": Quartz.kCGEventFlagMaskAlternate,
        "super": Quartz.kCGEventFlagMaskCommand,
        "meta": Quartz.kCGEventFlagMaskCommand,
        "option": Quartz.kCGEventFlagMaskAlternate,
    }


class MacOSPlatformProvider(PlatformProvider):
    """Full macOS implementation backed by PyObjC (Quartz / AppKit)."""

    def __init__(self):
        self._recorder = FfmpegRecorder()
        self._scale = None

    # ── Permissions / display helpers ────────────────────────────────────

    @property
    def accessibility_trusted(self) -> bool:
        """True when the process holds the Accessibility permission."""
        try:
            from ApplicationServices import AXIsProcessTrusted
            return bool(AXIsProcessTrusted())
        except Exception:
            return False

    def _require_accessibility(self, method: str):
        if not self.accessibility_trusted:
            raise OSError(
                f"{method} requires the Accessibility permission. Grant it in "
                "System Settings > Privacy & Security > Accessibility for "
                "the Python process running Reasonix, then retry.")

    def _scale_factor(self) -> float:
        """Main display backing scale (Retina = 2.0), cached."""
        if self._scale is None:
            try:
                from AppKit import NSScreen
                self._scale = float(NSScreen.mainScreen().backingScaleFactor())
            except Exception:
                self._scale = 1.0
        return self._scale

    def _to_points(self, x: int, y: int) -> tuple[float, float]:
        scale = self._scale_factor()
        return (x / scale, y / scale)

    def _from_points(self, x: float, y: float) -> tuple[int, int]:
        scale = self._scale_factor()
        return (int(x * scale), int(y * scale))

    # ── Mouse ────────────────────────────────────────────────────────────

    def _post_mouse(self, event_type: str, pos: tuple[float, float],
                    button: int = 0):
        import Quartz
        event = Quartz.CGEventCreateMouseEvent(
            None, getattr(Quartz, event_type), pos, button)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def mouse_move(self, x: int, y: int) -> None:
        self._require_accessibility("mouse_move")
        self._post_mouse("kCGEventMouseMoved", self._to_points(x, y))

    def mouse_click(self, x: int, y: int, button: str = "left",
                    count: int = 1, duration: float = 0.0) -> None:
        if button not in _BUTTON_NUMBERS:
            raise ValueError(f"Unknown button: {button}")
        self._require_accessibility("mouse_click")
        number = _BUTTON_NUMBERS[button]
        count = clamp_count(count)
        pos = self._to_points(x, y)

        self._post_mouse("kCGEventMouseMoved", pos)
        time.sleep(MOVE_SETTLE)

        # Same human-like rhythm as Windows/Linux (single source in common).
        for _ in range(count):
            self._post_mouse(_MOUSE_DOWN_EVENTS[number], pos, number)
            time.sleep(PRESS_HOLD)
            self._post_mouse(_MOUSE_UP_EVENTS[number], pos, number)
            if _ < count - 1:
                time.sleep(CLICK_GAP)

        if duration:
            time.sleep(min(duration, MAX_DURATION))

    def mouse_drag(self, from_x: int, from_y: int,
                   to_x: int, to_y: int, duration: float = 0.5) -> None:
        self._require_accessibility("mouse_drag")
        import Quartz
        start = self._to_points(from_x, from_y)
        points, delay = drag_plan((from_x, from_y), (to_x, to_y), duration)

        self._post_mouse("kCGEventMouseMoved", start)
        self._post_mouse("kCGEventLeftMouseDown", start, 0)
        time.sleep(MOVE_SETTLE)
        try:
            for cx, cy in points:
                self._post_mouse("kCGEventLeftMouseDragged",
                                 self._to_points(cx, cy), 0)
                time.sleep(delay)
        finally:
            self._post_mouse("kCGEventLeftMouseUp", start, 0)

    def mouse_scroll(self, x: int, y: int, amount: int,
                     direction: str = "vertical") -> None:
        self._require_accessibility("mouse_scroll")
        import Quartz
        self._post_mouse("kCGEventMouseMoved", self._to_points(x, y))
        time.sleep(MOVE_SETTLE)

        # macOS natural scrolling: a positive axis delta scrolls content up.
        positive = direction in ("up", "right")
        delta = amount if positive else -amount
        field = (Quartz.kCGScrollWheelEventDeltaAxis2 if direction
                 in ("left", "right") else Quartz.kCGScrollWheelEventDeltaAxis1)
        event = Quartz.CGEventCreateScrollWheelEvent(
            None, Quartz.kCGScrollEventUnitLine,
            1 if direction in ("left", "right") else 1, delta)
        Quartz.CGEventSetIntegerValueField(event, field, delta)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(SCROLL_SETTLE)

    # ── Keyboard ─────────────────────────────────────────────────────────

    def _post_key(self, keycode: int, key_down: bool, flags: int = 0):
        import Quartz
        event = Quartz.CGEventCreateKeyboardEvent(None, keycode, key_down)
        if flags:
            Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def keyboard_type(self, text: str) -> None:
        """Type Unicode text via CGEventKeyboardSetUnicodeString (no keymap
        lookup needed — handles CJK/emoji natively, unlike Linux)."""
        if not text:
            return
        self._require_accessibility("keyboard_type")
        import Quartz
        for chunk in (text[i:i + 100] for i in range(0, len(text), 100)):
            down = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
            Quartz.CGEventKeyboardSetUnicodeString(down, len(chunk), chunk)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
            Quartz.CGEventKeyboardSetUnicodeString(up, len(chunk), chunk)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

    def _resolve_keycode(self, name: str) -> int:
        keycode = _mac_keycodes().get(normalize_key_name(name))
        if keycode is None:
            raise ValueError(f"Unknown key: {name}")
        return keycode

    def keyboard_press(self, keys: List[str]) -> None:
        if not keys:
            return
        self._require_accessibility("keyboard_press")
        *modifiers, final = keys
        flags = 0
        for mod in modifiers:
            flags |= _modifier_flags().get(normalize_key_name(mod), 0)
        keycode = self._resolve_keycode(final)

        import Quartz
        event = Quartz.CGEventCreateKeyboardEvent(None, keycode, True)
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        up = Quartz.CGEventCreateKeyboardEvent(None, keycode, False)
        Quartz.CGEventSetFlags(up, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

    def keyboard_key_down(self, key: str) -> None:
        self._require_accessibility("keyboard_key_down")
        self._post_key(self._resolve_keycode(key), True)

    def keyboard_key_up(self, key: str) -> None:
        self._require_accessibility("keyboard_key_up")
        self._post_key(self._resolve_keycode(key), False)

    # ── Screen ───────────────────────────────────────────────────────────

    def screenshot(self, rect: Optional[Tuple[int, int, int, int]] = None):
        import mss
        from PIL import Image
        with mss.mss() as sct:
            if rect is None:
                monitor = sct.monitors[0]
            else:
                left, top, right, bottom = rect
                monitor = {"left": left, "top": top,
                           "width": right - left, "height": bottom - top}
            shot = sct.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.rgb)

    def get_virtual_screen_rect(self) -> Tuple[int, int, int, int]:
        import Quartz
        max_displays = 32
        ids = (Quartz.CGDirectDisplayID * max_displays)()
        count = Quartz.CGGetActiveDisplayList(max_displays, ids, None)
        if not count:
            bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        else:
            bounds = Quartz.CGRectUnion(
                Quartz.CGDisplayBounds(ids[0]), Quartz.CGDisplayBounds(ids[0]),
                Quartz.CGRectNull)
            for i in range(1, count):
                bounds = Quartz.CGRectUnion(
                    bounds, Quartz.CGDisplayBounds(ids[i]), Quartz.CGRectNull)
        return self._from_points(bounds.origin.x, bounds.origin.y) + \
            self._from_points(bounds.size.width, bounds.size.height)

    # ── Window (CGWindowList) ────────────────────────────────────────────

    def _window_info_list(self) -> list[dict]:
        import Quartz
        return Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID) or []

    def _build_window_info(self, entry: dict) -> Optional[WindowInfo]:
        bounds = entry.get("kCGWindowBounds") or {}
        left = bounds.get("X", 0)
        top = bounds.get("Y", 0)
        width = bounds.get("Width", 0)
        height = bounds.get("Height", 0)
        if width < 10 or height < 10:
            return None
        title = entry.get("kCGWindowName") or ""
        if not title:
            return None
        pid = entry.get("kCGWindowOwnerPID") or 0
        scale = self._scale_factor()
        return WindowInfo(
            id=str(entry.get("kCGWindowNumber", 0)),
            title=title,
            process_name=entry.get("kCGWindowOwnerName") or "",
            process_id=pid,
            rect=self._from_points(left, top) + self._from_points(
                left + width, top + height),
            dpi=int(96 * scale),
            scale_factor=scale,
        )

    def list_windows(self) -> List[WindowInfo]:
        results: List[WindowInfo] = []
        for entry in self._window_info_list():
            info = self._build_window_info(entry)
            if info is not None:
                results.append(info)
        return results

    def get_window_rect(self, window_id: str) -> Tuple[int, int, int, int]:
        target = int(window_id)
        for entry in self._window_info_list():
            if entry.get("kCGWindowNumber") == target:
                bounds = entry.get("kCGWindowBounds") or {}
                left, top = bounds.get("X", 0), bounds.get("Y", 0)
                width, height = bounds.get("Width", 0), bounds.get("Height", 0)
                return self._from_points(left, top) + self._from_points(
                    left + width, top + height)
        raise OSError(f"Window not found: {window_id}")

    def activate_window(self, window_id: str) -> bool:
        target = int(window_id)
        pid = None
        for entry in self._window_info_list():
            if entry.get("kCGWindowNumber") == target:
                pid = entry.get("kCGWindowOwnerPID")
                break
        if pid is None:
            raise OSError(f"Window not found: {window_id}")
        from AppKit import NSApplicationActivateIgnoringOtherApps, \
            NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier(pid)
        if app is None:
            return False
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        time.sleep(0.05)
        return self.get_foreground_window() is not None and \
            self.get_foreground_window().process_id == pid

    def get_foreground_window(self) -> Optional[WindowInfo]:
        try:
            from AppKit import NSWorkspace
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            if front is None:
                return None
            pid = front.processIdentifier()
        except Exception:
            return None
        for entry in self._window_info_list():
            if entry.get("kCGWindowOwnerPID") == pid and \
                    entry.get("kCGWindowLayer", 1) == 0:
                return self._build_window_info(entry)
        return None

    # ── Recording ────────────────────────────────────────────────────────

    def start_recording(self, output_path: Path,
                        rect: Optional[Tuple[int, int, int, int]] = None) -> bool:
        """Record the screen via FFmpeg avfoundation (macOS capture)."""

        def args_builder(capture_rect):
            args = [
                "ffmpeg", "-y",
                "-f", "avfoundation",
                "-framerate", "15",
                "-i", "1:none",  # screen 1, no audio
            ]
            if capture_rect:
                left, top, right, bottom = capture_rect
                args += ["-vf", f"crop={right - left}:{bottom - top}:{left}:{top}"]
            args += [str(output_path)]
            return args

        return self._recorder.start(output_path, args_builder, rect)

    def stop_recording(self) -> Optional[Path]:
        return self._recorder.stop()
