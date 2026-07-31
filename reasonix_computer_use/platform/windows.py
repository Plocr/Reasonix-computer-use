"""
Windows PlatformProvider — full implementation using Win32 APIs.

All coordinates accepted by public methods are PHYSICAL screen pixels.
Per-Monitor DPI V2 awareness is managed per-thread.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess
import time
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import List, Optional, Tuple

from .base import PlatformProvider, WindowInfo


# Pillow is imported lazily inside screenshot() to avoid blocking the entire
# module when Pillow is not yet installed at startup.


# ── Win32 constants ─────────────────────────────────────────────────────────

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ── Win32 argtypes for type safety ───────────────────────────────────────────
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = ctypes.c_bool
user32.GetCursorPos.argtypes = [ctypes.POINTER(ctypes.wintypes.POINT)]
user32.GetCursorPos.restype = ctypes.c_bool
user32.mouse_event.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                                ctypes.c_uint, ctypes.c_void_p]
user32.keybd_event.argtypes = [ctypes.c_byte, ctypes.c_byte, ctypes.c_uint,
                                ctypes.c_void_p]
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = ctypes.c_uint
user32.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR,
                                   ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(ctypes.c_bool,
                                ctypes.wintypes.HWND, ctypes.wintypes.LPARAM),
                                ctypes.wintypes.LPARAM]
user32.EnumWindows.restype = ctypes.c_bool

MOUSEEVENTF_LEFTDOWN   = 0x0002
MOUSEEVENTF_LEFTUP     = 0x0004
MOUSEEVENTF_RIGHTDOWN  = 0x0008
MOUSEEVENTF_RIGHTUP    = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP   = 0x0040
MOUSEEVENTF_WHEEL      = 0x0800
MOUSEEVENTF_HWHEEL     = 0x1000

BUTTON_FLAGS = {
    "left":   (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right":  (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

# Virtual Key Codes
VK_MAP: dict = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "backspace": 0x08,
    "delete": 0x2E, "escape": 0x1B, "esc": 0x1B, "space": 0x20,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "insert": 0x2D, "pause": 0x13, "capslock": 0x14,
    "printscreen": 0x2C, "prtsc": 0x2C, "apps": 0x5D,
    "numlock": 0x90, "scrolllock": 0x91,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2,
    "media_play_pause": 0xB3,
    "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
    "win": 0x5B, "meta": 0x5B, "command": 0x5B, "option": 0x12,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}

# Populate modifier aliases (Modifier+Key combos are resolved at press time)
MODIFIER_ALIASES = {
    "control": "ctrl", "cmd": "win", "super": "win",
    "opt": "alt", "altgr": "alt",
}


def _resolve_vk(name: str) -> int:
    """Resolve a key name to its virtual key code."""
    key = MODIFIER_ALIASES.get(name.lower(), name.lower())
    vk = VK_MAP.get(key)
    if vk is not None:
        return vk
    # Single printable ASCII → uppercase VK
    if len(key) == 1:
        return ord(key.upper())
    raise ValueError(f"Unknown key: {name}")


# ── DPI helpers ─────────────────────────────────────────────────────────────

def _enable_dpi_awareness() -> str:
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:
        shcore = ctypes.windll.shcore
        if shcore.SetProcessDpiAwareness(2) in (0, 0x80070005):
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass
    return "unaware"


DPI_AWARENESS = _enable_dpi_awareness()


@contextmanager
def _physical_pixel_context():
    """Ensure the current thread uses Per-Monitor V2 DPI awareness."""
    previous = None
    try:
        setter = user32.SetThreadDpiAwarenessContext
        setter.restype = ctypes.c_void_p
        setter.argtypes = [ctypes.c_void_p]
        previous = setter(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        previous = None
    try:
        yield
    finally:
        if previous is not None:
            try:
                user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))
            except (AttributeError, OSError):
                pass


# ── WindowsPlatformProvider ─────────────────────────────────────────────────

class WindowsPlatformProvider(PlatformProvider):
    """Full Windows implementation backed by Win32 APIs."""

    def __init__(self):
        self._dpi_mode = DPI_AWARENESS
        self._recording_process: Optional[subprocess.Popen] = None
        self._recording_output: Optional[Path] = None

    # ── Mouse ────────────────────────────────────────────────────────────

    def mouse_move(self, x: int, y: int) -> None:
        x, y = int(x), int(y)  # Force Python int for ctypes compatibility
        with _physical_pixel_context():
            if not user32.SetCursorPos(x, y):
                raise ctypes.WinError()
            # Verify
            actual = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(actual))
            if (actual.x, actual.y) != (x, y):
                raise OSError(
                    f"Mouse physical coordinate mismatch: "
                    f"requested=({x},{y}), actual=({actual.x},{actual.y})"
                )

    def mouse_click(self, x: int, y: int, button: str = "left",
                    count: int = 1, duration: float = 0.0) -> None:
        x, y = int(x), int(y)  # Force Python int for ctypes
        if button not in BUTTON_FLAGS:
            raise ValueError(f"Unknown button: {button}")
        down_flag, up_flag = BUTTON_FLAGS[button]
        count = max(1, min(int(count), 10))

        self.mouse_move(x, y)
        time.sleep(0.03)

        for i in range(count):
            user32.mouse_event(down_flag, 0, 0, 0, 0)
            try:
                time.sleep(0.02)
            finally:
                user32.mouse_event(up_flag, 0, 0, 0, 0)
            if i + 1 < count:
                time.sleep(0.05)

        if duration:
            time.sleep(min(duration, 5.0))

    def mouse_drag(self, from_x: int, from_y: int,
                   to_x: int, to_y: int, duration: float = 0.5) -> None:
        from_x, from_y = int(from_x), int(from_y)
        to_x, to_y = int(to_x), int(to_y)
        button = "left"
        down_flag, up_flag = BUTTON_FLAGS[button]
        duration = max(0.05, min(float(duration), 5.0))
        steps = max(2, min(int(duration * 40), 60))
        pressed = False

        with _physical_pixel_context():
            if not user32.SetCursorPos(from_x, from_y):
                raise ctypes.WinError()
            try:
                user32.mouse_event(down_flag, 0, 0, 0, 0)
                pressed = True
                time.sleep(0.03)
                delay = duration / steps
                for step in range(1, steps + 1):
                    cx = from_x + (to_x - from_x) * step // steps
                    cy = from_y + (to_y - from_y) * step // steps
                    if not user32.SetCursorPos(cx, cy):
                        raise ctypes.WinError()
                    time.sleep(delay)
            finally:
                if pressed:
                    user32.mouse_event(up_flag, 0, 0, 0, 0)

    def mouse_scroll(self, x: int, y: int, amount: int,
                     direction: str = "vertical") -> None:
        x, y = int(x), int(y)
        self.mouse_move(x, y)
        time.sleep(0.03)

        positive = direction in ("up", "right")
        delta = amount * 120 if positive else -amount * 120
        flag = MOUSEEVENTF_HWHEEL if direction in ("left", "right") else MOUSEEVENTF_WHEEL
        user32.mouse_event(flag, 0, 0, delta, 0)
        time.sleep(0.05)

    # ── Keyboard ─────────────────────────────────────────────────────────

    def keyboard_type(self, text: str) -> None:
        """Type raw Unicode text via SendInput.

        Handles UTF-16 surrogate pairs (emoji, CJK extension B, ...) and
        splits long text into batches because SendInput accepts at most
        65536 input events per call.
        """
        if not text:
            return
        import ctypes
        INPUT_KEYBOARD = 1
        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        class INPUT_UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [
                ("type", wintypes.DWORD),
                ("union", INPUT_UNION),
            ]

        def utf16_units(value: str):
            """Yield UTF-16 code units, expanding astral chars to surrogates."""
            for ch in value:
                code = ord(ch)
                if code <= 0xFFFF:
                    yield code
                else:
                    code -= 0x10000
                    yield 0xD800 + (code >> 10)
                    yield 0xDC00 + (code & 0x3FF)

        units = list(utf16_units(text))
        # SendInput caps at 65536 INPUT events; keep well under that per call.
        max_events_per_call = 2000  # 1000 code units → 2000 events (down+up)
        for offset in range(0, len(units), max_events_per_call // 2):
            chunk = units[offset:offset + max_events_per_call // 2]
            inputs = (INPUT * (len(chunk) * 2))()
            for i, code in enumerate(chunk):
                for j, flags in enumerate((KEYEVENTF_UNICODE,
                                           KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
                    slot = inputs[i * 2 + j]
                    slot.type = INPUT_KEYBOARD
                    slot.union.ki.wVk = 0
                    slot.union.ki.wScan = code
                    slot.union.ki.dwFlags = flags
                    slot.union.ki.time = 0
                    slot.union.ki.dwExtraInfo = None

            sent = ctypes.windll.user32.SendInput(
                len(inputs), inputs, ctypes.sizeof(INPUT))
            if sent != len(inputs):
                raise OSError(
                    f"SendInput injected {sent}/{len(inputs)} keyboard events")

    def keyboard_press(self, keys: List[str]) -> None:
        """Press a key combination (e.g. ["CTRL", "C"]).

        Modifiers are held down, the last key is pressed and released,
        then modifiers are released in reverse order.
        """
        if not keys:
            return

        # Split into modifiers and the final key
        *modifiers, final = keys
        vk_final = _resolve_vk(final)
        vk_mods = [_resolve_vk(m) for m in modifiers]
        all_vks = vk_mods + [vk_final]

        # Key down all in order
        for vk in all_vks:
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.02)

        # Key up in reverse order
        for vk in reversed(all_vks):
            user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP = 2
            time.sleep(0.02)

    def keyboard_key_down(self, key: str) -> None:
        vk = _resolve_vk(key)
        user32.keybd_event(vk, 0, 0, 0)

    def keyboard_key_up(self, key: str) -> None:
        vk = _resolve_vk(key)
        user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP

    # ── Screen ───────────────────────────────────────────────────────────

    def screenshot(self, rect: Optional[Tuple[int, int, int, int]] = None) -> Image:
        from PIL import Image, ImageGrab  # Lazy import — Pillow may be installed after startup
        with _physical_pixel_context():
            if rect is not None:
                return ImageGrab.grab(bbox=rect, all_screens=True)
            return ImageGrab.grab(all_screens=True)

    def get_virtual_screen_rect(self) -> Tuple[int, int, int, int]:
        left = user32.GetSystemMetrics(76)    # SM_XVIRTUALSCREEN
        top = user32.GetSystemMetrics(77)     # SM_YVIRTUALSCREEN
        width = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        return (left, top, left + width, top + height)

    # ── Window ───────────────────────────────────────────────────────────

    def list_windows(self) -> List[WindowInfo]:
        results: List[WindowInfo] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            title = self._window_text(hwnd)
            if not title:
                return True
            try:
                rect = self._get_window_rect(hwnd)
                if rect[2] - rect[0] < 10 or rect[3] - rect[1] < 10:
                    return True
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                dpi = self._window_dpi(hwnd)
                info = WindowInfo(
                    id=str(hwnd),
                    title=title,
                    process_id=pid.value,
                    rect=rect,
                    dpi=dpi,
                    scale_factor=dpi / 96.0,
                )
                results.append(info)
            except (ValueError, OSError):
                pass
            return True

        user32.EnumWindows(callback, 0)
        return results

    def get_window_rect(self, window_id: str) -> Tuple[int, int, int, int]:
        hwnd = int(window_id)
        return self._get_window_rect(hwnd)

    def activate_window(self, window_id: str) -> bool:
        hwnd = int(window_id)
        if not hwnd or not user32.IsWindow(hwnd):
            raise OSError("Invalid target window")

        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE

        foreground = user32.GetForegroundWindow()
        ft = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        ct = kernel32.GetCurrentThreadId()
        attached = bool(ft and ft != ct)
        try:
            if attached:
                user32.AttachThreadInput(ct, ft, True)
            user32.BringWindowToTop(hwnd)
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(ct, ft, False)

        return user32.GetForegroundWindow() == hwnd

    def get_foreground_window(self) -> Optional[WindowInfo]:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        title = self._window_text(hwnd)
        rect = self._get_window_rect(hwnd)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        dpi = self._window_dpi(hwnd)
        return WindowInfo(
            id=str(hwnd),
            title=title,
            process_id=pid.value,
            rect=rect,
            dpi=dpi,
            scale_factor=dpi / 96.0,
        )

    # ── Recording ────────────────────────────────────────────────────────

    def start_recording(self, output_path: Path,
                        rect: Optional[Tuple[int, int, int, int]] = None) -> bool:
        """Try system-native recording via FFmpeg gdigrab.

        Falls back to FFmpeg gdigrab if available.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._recording_output = output_path

        # Build ffmpeg args — gdigrab uses -i desktop for full screen
        args = [
            "ffmpeg", "-y",
            "-f", "gdigrab",
            "-framerate", "15",
        ]
        if rect:
            left, top, right, bottom = rect
            args += [
                "-offset_x", str(left),
                "-offset_y", str(top),
                "-video_size", f"{right - left}x{bottom - top}",
            ]
        args += ["-i", "desktop", str(output_path)]

        try:
            self._recording_process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except FileNotFoundError:
            # FFmpeg not available — can't record
            return False

    def stop_recording(self) -> Optional[Path]:
        if self._recording_process:
            self._recording_process.terminate()
            try:
                self._recording_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._recording_process.kill()
            self._recording_process = None
            return self._recording_output
        return None

    # ── Internal helpers ─────────────────────────────────────────────────

    @staticmethod
    def _window_text(hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    @staticmethod
    def _window_class(hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    @staticmethod
    def _get_window_rect(hwnd: int) -> Tuple[int, int, int, int]:
        rect = wintypes.RECT()
        with _physical_pixel_context():
            try:
                dwmapi = ctypes.windll.dwmapi
                if dwmapi.DwmGetWindowAttribute(
                    hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)
                ) == 0:
                    return rect.left, rect.top, rect.right, rect.bottom
            except (AttributeError, OSError):
                pass
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                raise ctypes.WinError()
        return rect.left, rect.top, rect.right, rect.bottom

    @staticmethod
    def _window_dpi(hwnd: int) -> int:
        try:
            return int(user32.GetDpiForWindow(hwnd))
        except (AttributeError, OSError):
            return 96
