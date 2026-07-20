"""Cross-platform window management backend.

Windows: uses existing windows.py (user32.dll).
macOS: uses Quartz / AppleScript.
Linux: uses Xlib / wmctrl.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Any

from .platform_backend import IS_WINDOWS, IS_MACOS, IS_LINUX


@dataclass(frozen=True)
class WindowInfo:
    id: int  # hwnd on Windows, window id on Linux, None on macOS
    title: str
    class_name: str  # app name on macOS, class on Linux
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    pid: int = 0
    process_path: str = ""


def list_windows(visible_only: bool = True, min_width: int = 10) -> list[WindowInfo]:
    """List all windows across platforms."""
    if IS_WINDOWS:
        from . import windows
        return windows.list_windows(visible_only, min_width)
    if IS_MACOS:
        return _list_windows_macos(visible_only, min_width)
    if IS_LINUX:
        return _list_windows_linux(visible_only, min_width)
    return []


def resolve_window(identifier: str | None = None) -> WindowInfo:
    """Resolve a window by id/title/class."""
    if IS_WINDOWS:
        from . import windows
        return windows.resolve_window(identifier)
    if IS_MACOS:
        return _resolve_window_macos(identifier)
    if IS_LINUX:
        return _resolve_window_linux(identifier)
    raise NotImplementedError("Unsupported platform")


def activate_window(window_id: int) -> None:
    """Bring a window to the foreground."""
    if IS_WINDOWS:
        from . import windows
        return windows.activate_window(window_id)
    if IS_MACOS:
        return _activate_window_macos(window_id)
    if IS_LINUX:
        return _activate_window_linux(window_id)


def get_window_rect(window_id: int) -> tuple[int, int, int, int]:
    """Get window bounds in physical pixels."""
    if IS_WINDOWS:
        from . import windows
        return windows.get_window_rect(window_id)
    if IS_MACOS:
        return _get_window_rect_macos(window_id)
    if IS_LINUX:
        return _get_window_rect_linux(window_id)
    raise NotImplementedError("Unsupported platform")


# ─── macOS backends ──────────────────────────────────────────────────────────

def _list_windows_macos(visible_only: bool, min_width: int) -> list[WindowInfo]:
    """List windows on macOS using Quartz."""
    try:
        import Quartz
        from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionAll, kCGNullWindowID
        from Quartz import kCGWindowBounds, kCGWindowName, kCGWindowNumber, kCGWindowOwnerPID

        window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)
        results = []
        for window in window_list:
            try:
                title = window.get(kCGWindowName, "")
                if not title:
                    continue
                bounds = window.get(kCGWindowBounds, {})
                left = int(bounds.get("X", 0))
                top = int(bounds.get("Y", 0))
                width = int(bounds.get("Width", 0))
                height = int(bounds.get("Height", 0))
                if width < min_width or height < 10:
                    continue
                results.append(WindowInfo(
                    id=int(window.get(kCGWindowNumber, 0)),
                    title=title,
                    class_name=window.get("kCGWindowOwnerName", ""),
                    rect=(left, top, left + width, top + height),
                    pid=int(window.get(kCGWindowOwnerPID, 0)),
                ))
            except (ValueError, TypeError):
                continue
        return results
    except ImportError:
        return []


def _resolve_window_macos(identifier: str | None) -> WindowInfo:
    """Resolve a window on macOS."""
    windows = list_windows()
    if identifier:
        for w in windows:
            if identifier.lower() in w.title.lower():
                return w
    if windows:
        return windows[0]
    raise ValueError("No window found")


def _activate_window_macos(window_id: int) -> None:
    """Activate a window on macOS using AppleScript."""
    import subprocess
    script = '''
    tell application "System Events"
        set frontmost of (first process whose unix id is {pid}) to true
    end tell
    '''.format(pid=_get_pid_from_window_macos(window_id))
    subprocess.run(["osascript", "-e", script], capture_output=True)


def _get_pid_from_window_macos(window_id: int) -> int:
    """Get PID from window ID on macOS."""
    import Quartz
    from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionAll, kCGNullWindowID
    from Quartz import kCGWindowNumber, kCGWindowOwnerPID
    window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)
    for window in window_list:
        if int(window.get(kCGWindowNumber, 0)) == window_id:
            return int(window.get(kCGWindowOwnerPID, 0))
    return 0


def _get_window_rect_macos(window_id: int) -> tuple[int, int, int, int]:
    """Get window rect on macOS."""
    import Quartz
    from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionAll, kCGNullWindowID
    from Quartz import kCGWindowBounds, kCGWindowNumber
    window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)
    for window in window_list:
        if int(window.get(kCGWindowNumber, 0)) == window_id:
            bounds = window.get(kCGWindowBounds, {})
            left = int(bounds.get("X", 0))
            top = int(bounds.get("Y", 0))
            width = int(bounds.get("Width", 0))
            height = int(bounds.get("Height", 0))
            return (left, top, left + width, top + height)
    raise ValueError("Window not found")


# ─── Linux backends ──────────────────────────────────────────────────────────

def _list_windows_linux(visible_only: bool, min_width: int) -> list[WindowInfo]:
    """List windows on Linux using Xlib or wmctrl."""
    try:
        import subprocess
        output = subprocess.run(
            ["wmctrl", "-l", "-G", "-p"],
            capture_output=True, text=True, timeout=5
        )
        if output.returncode != 0:
            return []
        results = []
        for line in output.stdout.splitlines():
            parts = line.split(None, 8)
            if len(parts) < 8:
                continue
            win_id = int(parts[0], 16)
            pid = int(parts[2])
            left = int(parts[3])
            top = int(parts[4])
            width = int(parts[5])
            height = int(parts[6])
            title = parts[8] if len(parts) > 8 else ""
            if width < min_width or height < 10:
                continue
            results.append(WindowInfo(
                id=win_id, title=title, class_name=parts[1] if len(parts) > 1 else "",
                rect=(left, top, left + width, top + height), pid=pid,
            ))
        return results
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return []


def _resolve_window_linux(identifier: str | None) -> WindowInfo:
    """Resolve a window on Linux."""
    windows = list_windows()
    if identifier:
        for w in windows:
            if identifier.lower() in w.title.lower():
                return w
    if windows:
        return windows[0]
    raise ValueError("No window found")


def _activate_window_linux(window_id: int) -> None:
    """Activate a window on Linux using wmctrl."""
    import subprocess
    subprocess.run(["wmctrl", "-i", "-a", hex(window_id)], capture_output=True, timeout=5)


def _get_window_rect_linux(window_id: int) -> tuple[int, int, int, int]:
    """Get window rect on Linux."""
    import subprocess
    output = subprocess.run(
        ["wmctrl", "-l", "-G"],
        capture_output=True, text=True, timeout=5
    )
    if output.returncode == 0:
        for line in output.stdout.splitlines():
            parts = line.split(None, 8)
            if len(parts) >= 7:
                win_id = int(parts[0], 16)
                if win_id == window_id:
                    left = int(parts[3])
                    top = int(parts[4])
                    width = int(parts[5])
                    height = int(parts[6])
                    return (left, top, left + width, top + height)
    raise ValueError("Window not found")
