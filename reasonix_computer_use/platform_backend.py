"""Platform detection and cross-platform backend selection.

Provides unified interfaces for:
- Screenshot (via mss)
- Mouse control (via pynput)
- Keyboard control (via pynput)
- Window management (platform-specific backends)
- Process management (via psutil)
"""

from __future__ import annotations

import platform
import sys
from typing import Any

PLATFORM = platform.system().lower()  # "windows", "darwin", "linux"
IS_WINDOWS = PLATFORM == "windows"
IS_MACOS = PLATFORM == "darwin"
IS_LINUX = PLATFORM == "linux"


def _mss_grab(region: tuple[int, int, int, int] | None = None):
    """Cross-platform screenshot via mss.

    region: (left, top, width, height) in physical pixels.
    Returns a PIL Image.
    """
    from PIL import Image
    import mss

    with mss.mss() as screen_capture:
        if region:
            monitor = {"left": region[0], "top": region[1], "width": region[2], "height": region[3]}
        else:
            monitor = screen_capture.monitors[0]  # primary monitor (virtual screen)
        raw = screen_capture.grab(monitor)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def _pynput_mouse():
    """Get pynput mouse controller."""
    from pynput.mouse import Controller, Button
    return Controller(), Button


def _pynput_keyboard():
    """Get pynput keyboard controller."""
    from pynput.keyboard import Controller, Key
    return Controller(), Key


class MouseBackend:
    """Cross-platform mouse control via pynput."""

    def __init__(self) -> None:
        self._controller, self._button = _pynput_mouse()

    def move(self, x: int, y: int) -> None:
        self._controller.position = (x, y)

    def click(self, button: str = "left", double: bool = False) -> None:
        btn = {"left": self._button.left, "right": self._button.right, "middle": self._button.middle}.get(button, self._button.left)
        self._controller.click(btn, 2 if double else 1)

    def scroll(self, direction: str = "up", lines: int = 3) -> None:
        delta = lines * 3 if direction == "up" else -lines * 3
        self._controller.scroll(0, delta)

    @property
    def position(self) -> tuple[int, int]:
        return self._controller.position


class KeyboardBackend:
    """Cross-platform keyboard control via pynput."""

    def __init__(self) -> None:
        self._controller, self._key = _pynput_keyboard()

    def press_key(self, key: str, modifiers: list[str] | None = None) -> None:
        """Press a key with optional modifiers."""
        modifier_keys = []
        if modifiers:
            for mod in modifiers:
                mod_lower = mod.lower()
                if mod_lower in ("ctrl", "control"):
                    modifier_keys.append(self._key.ctrl)
                elif mod_lower in ("alt",):
                    modifier_keys.append(self._key.alt)
                elif mod_lower in ("shift",):
                    modifier_keys.append(self._key.shift)
                elif mod_lower in ("win", "meta", "cmd"):
                    modifier_keys.append(self._key.cmd)

        key_to_press = self._resolve_key(key)

        if modifier_keys:
            with self._controller.pressed(*modifier_keys):
                self._controller.press(key_to_press)
                self._controller.release(key_to_press)
        else:
            self._controller.press(key_to_press)
            self._controller.release(key_to_press)

    def type_text(self, text: str, interval: float = 0.01) -> None:
        """Type a string with a small delay between characters."""
        import time
        for char in text:
            self._controller.press(char)
            self._controller.release(char)
            if interval > 0:
                time.sleep(interval)

    def _resolve_key(self, key: str):
        """Resolve a key name to pynput Key or character."""
        key_lower = key.lower()
        if hasattr(self._key, key_lower):
            return getattr(self._key, key_lower)
        if len(key) == 1:
            return key
        return key


def screenshot(region: tuple[int, int, int, int] | None = None):
    """Take a screenshot. Returns PIL Image."""
    return _mss_grab(region)


def get_mouse() -> MouseBackend:
    return MouseBackend()


def get_keyboard() -> KeyboardBackend:
    return KeyboardBackend()


def list_monitors() -> list[dict[str, Any]]:
    """List all monitors with their physical bounds."""
    import mss
    with mss.mss() as screen_capture:
        result = []
        for index, monitor in enumerate(screen_capture.monitors):
            # monitors[0] is the virtual screen, monitors[1+] are physical
            result.append(
                {"index": index, "left": monitor["left"], "top": monitor["top"],
                 "width": monitor["width"], "height": monitor["height"],
                 "primary": index == 0}
            )
        return result


def virtual_screen() -> dict[str, Any]:
    """Get the bounding box of all monitors combined."""
    import mss
    with mss.mss() as screen_capture:
        monitor = screen_capture.monitors[0]
        return {"left": monitor["left"], "top": monitor["top"],
                "width": monitor["width"], "height": monitor["height"]}
