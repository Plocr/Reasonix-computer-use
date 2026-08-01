# Reasonix Computer Use — Platform abstraction layer

from .base import PlatformProvider, WindowInfo


class UnsupportedPlatformProvider(PlatformProvider):
    """Fail-fast stub for platforms without a native implementation.

    macOS (AXAPI) and Linux (AT-SPI2) backends are planned but not yet
    implemented.  Using the Windows provider on those platforms would crash
    at import time (``ctypes.windll`` does not exist), so we raise a clear
    error instead of pretending to work.
    """

    def __init__(self, platform_name: str):
        self._platform_name = platform_name

    def _unsupported(self, method: str):
        raise NotImplementedError(
            f"{self._platform_name} backend is not implemented yet "
            f"({method}); Windows 10/11 is the only supported platform")

    def mouse_move(self, x, y): self._unsupported("mouse_move")
    def mouse_click(self, x, y, button="left", count=1, duration=0.0): self._unsupported("mouse_click")
    def mouse_drag(self, from_x, from_y, to_x, to_y, duration=0.5): self._unsupported("mouse_drag")
    def mouse_scroll(self, x, y, amount, direction="vertical"): self._unsupported("mouse_scroll")
    def keyboard_type(self, text): self._unsupported("keyboard_type")
    def keyboard_press(self, keys): self._unsupported("keyboard_press")
    def keyboard_key_down(self, key): self._unsupported("keyboard_key_down")
    def keyboard_key_up(self, key): self._unsupported("keyboard_key_up")
    def screenshot(self, rect=None):
        # Cross-platform screenshot works everywhere Pillow is installed.
        from PIL import ImageGrab
        if rect is not None:
            return ImageGrab.grab(bbox=rect, all_screens=True)
        return ImageGrab.grab(all_screens=True)
    def get_virtual_screen_rect(self): self._unsupported("get_virtual_screen_rect")
    def list_windows(self): self._unsupported("list_windows")
    def get_window_rect(self, window_id): self._unsupported("get_window_rect")
    def activate_window(self, window_id): self._unsupported("activate_window")
    def get_foreground_window(self): self._unsupported("get_foreground_window")
    def start_recording(self, output_path, rect=None): self._unsupported("start_recording")
    def stop_recording(self): self._unsupported("stop_recording")


def get_platform() -> PlatformProvider:
    """Return the correct PlatformProvider for the current OS."""
    import sys
    if sys.platform == "win32":
        from .windows import WindowsPlatformProvider
        return WindowsPlatformProvider()
    if sys.platform == "linux":
        from .linux import LinuxPlatformProvider
        return LinuxPlatformProvider()
    if sys.platform == "darwin":
        from .macos import MacOSPlatformProvider
        return MacOSPlatformProvider()
    return UnsupportedPlatformProvider("Linux")

# WindowsPlatformProvider is imported lazily inside get_platform() because
# reasonix_computer_use/platform/windows.py executes ``ctypes.windll`` at
# module level, which does not exist on macOS/Linux.  The module-level
# re-export below is therefore platform-conditional: it keeps the historical
# ``from reasonix_computer_use.platform import WindowsPlatformProvider``
# import working on Windows without crashing the whole package elsewhere.
import sys as _sys
if _sys.platform == "win32":
    from .windows import WindowsPlatformProvider  # noqa: E402
else:
    WindowsPlatformProvider = UnsupportedPlatformProvider  # type: ignore[misc,assignment]

__all__ = [
    "PlatformProvider",
    "WindowInfo",
    "get_platform",
    "WindowsPlatformProvider",
    "UnsupportedPlatformProvider",
]
