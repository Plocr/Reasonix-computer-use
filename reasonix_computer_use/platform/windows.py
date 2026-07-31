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

# ── HWND-typed signatures (x64 correctness: without these, ctypes passes
#    handles as 32-bit ints and truncates returned handles) ───────────────
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = ctypes.c_bool
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = ctypes.c_bool
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = ctypes.c_bool
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = ctypes.c_bool
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = ctypes.c_bool
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = ctypes.c_bool
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_bool]
user32.AttachThreadInput.restype = ctypes.c_bool
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = ctypes.c_bool
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = ctypes.c_uint
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

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
    "super": 0x5B, "win": 0x5B, "meta": 0x5B, "command": 0x5B, "option": 0x12,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}


def _resolve_vk(name: str) -> int:
    """Resolve a key name to its virtual key code."""
    key = normalize_key_name(name)
    vk = VK_MAP.get(key)
    if vk is not None:
        return vk
    # Single printable ASCII → uppercase VK
    if len(key) == 1:
        return ord(key.upper())
    raise ValueError(f"Unknown key: {name}")


# ── SendInput structures (module-level; shared by keyboard_type/press) ────
# The SDK INPUT union size is determined by its LARGEST member (MOUSEINPUT
# = 32 bytes on x64), so sizeof(INPUT) MUST be 40 — a union with only
# KEYBDINPUT yields 32 and SendInput rejects the whole batch.
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT),
                ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]


assert ctypes.sizeof(_INPUT) == 40, \
    f"INPUT struct must be 40 bytes on x64, got {ctypes.sizeof(_INPUT)}"

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


def _send_input(events: list[tuple[int, int, int]]) -> None:
    """Send keyboard INPUT events (vk, scan, flags) and verify injection.

    Raises OSError with a UIPI hint when the target window is elevated and
    the OS blocked the injection (previously silent via keybd_event).
    """
    inputs = (_INPUT * len(events))()
    for i, (vk, scan, flags) in enumerate(events):
        slot = inputs[i]
        slot.type = INPUT_KEYBOARD
        slot.union.ki.wVk = vk
        slot.union.ki.wScan = scan
        slot.union.ki.dwFlags = flags
        slot.union.ki.time = 0
        slot.union.ki.dwExtraInfo = None
    sent = user32.SendInput(len(inputs), inputs, ctypes.sizeof(_INPUT))
    if sent != len(inputs):
        try:
            last_error = ctypes.windll.kernel32.GetLastError()
        except (AttributeError, OSError):
            last_error = None
        hint = ""
        if last_error == 5:
            hint = (" (UIPI: 目标窗口以管理员权限运行，键盘注入被系统拦截；"
                    "请以管理员权限运行 Reasonix，或以普通权限启动目标应用)")
        raise OSError(
            f"SendInput injected {sent}/{len(inputs)} keyboard events"
            f" (GetLastError={last_error}){hint}")


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
        self._recorder = FfmpegRecorder()

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
        count = clamp_count(count)

        self.mouse_move(x, y)
        time.sleep(MOVE_SETTLE)

        for i in range(count):
            user32.mouse_event(down_flag, 0, 0, 0, 0)
            try:
                time.sleep(PRESS_HOLD)
            finally:
                user32.mouse_event(up_flag, 0, 0, 0, 0)
            if i + 1 < count:
                time.sleep(CLICK_GAP)

        if duration:
            time.sleep(min(duration, MAX_DURATION))

    def mouse_drag(self, from_x: int, from_y: int,
                   to_x: int, to_y: int, duration: float = 0.5) -> None:
        from_x, from_y = int(from_x), int(from_y)
        to_x, to_y = int(to_x), int(to_y)
        button = "left"
        down_flag, up_flag = BUTTON_FLAGS[button]
        points, delay = drag_plan((from_x, from_y), (to_x, to_y), duration)
        pressed = False

        with _physical_pixel_context():
            if not user32.SetCursorPos(from_x, from_y):
                raise ctypes.WinError()
            try:
                user32.mouse_event(down_flag, 0, 0, 0, 0)
                pressed = True
                time.sleep(MOVE_SETTLE)
                for cx, cy in points:
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
        time.sleep(MOVE_SETTLE)

        positive = direction in ("up", "right")
        delta = amount * 120 if positive else -amount * 120
        flag = MOUSEEVENTF_HWHEEL if direction in ("left", "right") else MOUSEEVENTF_WHEEL
        user32.mouse_event(flag, 0, 0, delta, 0)
        time.sleep(SCROLL_SETTLE)

    # ── Keyboard ─────────────────────────────────────────────────────────

    def keyboard_type(self, text: str) -> None:
        """Type raw Unicode text via SendInput.

        Handles UTF-16 surrogate pairs (emoji, CJK extension B, ...) and
        splits long text into batches because SendInput accepts at most
        65536 input events per call.
        """
        if not text:
            return

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
            events = []
            for code in chunk:
                events.append((0, code, KEYEVENTF_UNICODE))
                events.append((0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
            _send_input(events)

    def keyboard_press(self, keys: List[str]) -> None:
        """Press a key combination (e.g. ["CTRL", "C"]).

        Modifiers are held down, the last key is pressed and released,
        then modifiers are released in reverse order.  Uses SendInput so a
        UIPI-blocked injection is detected instead of silently doing nothing.
        """
        if not keys:
            return

        # Split into modifiers and the final key
        *modifiers, final = keys
        vk_final = _resolve_vk(final)
        vk_mods = [_resolve_vk(m) for m in modifiers]
        all_vks = vk_mods + [vk_final]

        events = []
        for vk in all_vks:
            events.append((vk, 0, 0))  # KEYEVENTF_KEYDOWN
        for vk in reversed(all_vks):
            events.append((vk, 0, KEYEVENTF_KEYUP))
        _send_input(events)

    def keyboard_key_down(self, key: str) -> None:
        vk = _resolve_vk(key)
        _send_input([(vk, 0, 0)])

    def keyboard_key_up(self, key: str) -> None:
        vk = _resolve_vk(key)
        _send_input([(vk, 0, KEYEVENTF_KEYUP)])

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
        """Record the screen via FFmpeg gdigrab (Windows capture)."""

        def args_builder(capture_rect):
            args = [
                "ffmpeg", "-y",
                "-f", "gdigrab",
                "-framerate", "15",
            ]
            if capture_rect:
                left, top, right, bottom = capture_rect
                args += [
                    "-offset_x", str(left),
                    "-offset_y", str(top),
                    "-video_size", f"{right - left}x{bottom - top}",
                ]
            args += ["-i", "desktop", str(output_path)]
            return args

        return self._recorder.start(output_path, args_builder, rect)

    def stop_recording(self) -> Optional[Path]:
        return self._recorder.stop()

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
