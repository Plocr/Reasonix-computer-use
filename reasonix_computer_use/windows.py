"""Shared Windows DPI, window, and physical-coordinate helpers."""

from __future__ import annotations

import ctypes
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass


user32 = ctypes.windll.user32


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    rect: tuple[int, int, int, int]
    pid: int = 0
    process_path: str = ""


def enable_dpi_awareness() -> str:
    """Select the best available process DPI mode before creating UI objects."""
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


DPI_AWARENESS = enable_dpi_awareness()


@contextmanager
def physical_pixel_context():
    """Keep Win32 coordinates in per-monitor physical pixels on this thread."""
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
        if previous:
            try:
                user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))
            except (AttributeError, OSError):
                pass


def _window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return the DWM visible frame in physical pixels, with Win32 fallback."""
    rect = wintypes.RECT()
    with physical_pixel_context():
        try:
            dwmapi = ctypes.windll.dwmapi
            if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0:
                return rect.left, rect.top, rect.right, rect.bottom
        except (AttributeError, OSError):
            pass
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError()
    return rect.left, rect.top, rect.right, rect.bottom


def get_window_info(hwnd: int) -> WindowInfo:
    if not hwnd or not user32.IsWindow(hwnd):
        raise ValueError("Invalid window handle")
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process_path = ""
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
    if handle:
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                process_path = buffer.value
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    return WindowInfo(hwnd, _window_text(hwnd), _class_name(hwnd), get_window_rect(hwnd),
                      int(pid.value), process_path)


def list_windows(visible_only: bool = True, min_width: int = 10) -> list[WindowInfo]:
    results: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        if not _window_text(hwnd):
            return True
        try:
            info = get_window_info(hwnd)
        except (ValueError, OSError):
            return True
        left, top, right, bottom = info.rect
        if right - left >= min_width and bottom - top >= 10:
            results.append(info)
        return True

    user32.EnumWindows(callback, 0)
    return results


def resolve_window(identifier: str | None = None, method: str = "auto") -> WindowInfo:
    """Resolve hwnd, title substring, or exact class; default to foreground."""
    if not identifier:
        return get_window_info(user32.GetForegroundWindow())
    value = str(identifier).strip()
    if method in ("auto", "hwnd") and (value.isdigit() or value.lower().startswith("0x")):
        return get_window_info(int(value, 0))
    needle = value.casefold()
    windows = list_windows()
    if method == "class":
        for info in windows:
            if info.class_name.casefold() == needle:
                return info
    if method in ("auto", "title"):
        exact = [info for info in windows if info.title.casefold() == needle]
        if exact:
            return exact[0]
        if len(needle) <= 3:
            raise ValueError(f"Window not found by exact short title: {identifier}")
        ranked = sorted(windows, key=lambda info: (
            not info.title.casefold().startswith(needle),
            needle not in info.title.casefold(),
            len(info.title),
        ))
        if ranked and needle in ranked[0].title.casefold():
            return ranked[0]
    raise ValueError(f"Window not found: {identifier}")


def activate_window(hwnd: int) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    if not user32.SetForegroundWindow(hwnd) and user32.GetForegroundWindow() != hwnd:
        raise OSError("Windows denied foreground activation")


def virtual_screen() -> dict:
    return {
        "left": user32.GetSystemMetrics(76),
        "top": user32.GetSystemMetrics(77),
        "width": user32.GetSystemMetrics(78),
        "height": user32.GetSystemMetrics(79),
    }


def window_dpi(hwnd: int) -> int:
    try:
        return int(user32.GetDpiForWindow(hwnd))
    except (AttributeError, OSError):
        return 96
