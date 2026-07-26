"""
Hidden tools — raw hardware simulation with normalized coordinates.

These tools are NOT exposed to the host Agent directly.  They are called
internally by screen_interactor.  All coordinates MUST use normalized protocol
(CLAUDE_1024, GEMINI_1000, PIXEL, or ELEMENT_REF).  Conversion to physical
pixels is handled here.

Tools:
  mouse_action     — click, double_click, right_click, drag (normalized coords)
  keyboard_action  — type, press, key_down, key_up
  screenshot       — capture screen region
  screen_recorder  — start/stop system-native recording (outputs to user Downloads)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...protocol import CoordinateConverter, NormalizedCoord, CoordinateSpace
from ...platform import get_platform


class HiddenMouse:
    """Low-level mouse operations with normalized coordinates."""

    def __init__(self):
        self._platform = get_platform()
        self._converter = CoordinateConverter.from_system_index()

    def _refresh(self):
        self._converter = CoordinateConverter.from_system_index()

    def _to_physical(self, coord: NormalizedCoord) -> Tuple[int, int]:
        """Convert normalized to physical, refreshing scale_factor."""
        self._refresh()
        fg = self._platform.get_foreground_window()
        window_rect = fg.rect if fg else None
        return self._converter.to_physical(coord, window_rect=window_rect)

    def click(self, coord: NormalizedCoord, button: str = "left",
              count: int = 1) -> dict:
        """Click at normalized coordinate."""
        x, y = self._to_physical(coord)
        self._platform.mouse_click(x, y, button=button, count=count)
        return {"status": "ok", "action": "click", "button": button,
                "count": count, "physical": [x, y]}

    def double_click(self, coord: NormalizedCoord) -> dict:
        """Double-click at normalized coordinate."""
        return self.click(coord, count=2)

    def right_click(self, coord: NormalizedCoord) -> dict:
        """Right-click at normalized coordinate."""
        return self.click(coord, button="right")

    def drag(self, from_coord: NormalizedCoord, to_coord: NormalizedCoord,
             duration: float = 0.5) -> dict:
        """Click-and-drag from one normalized coordinate to another."""
        fx, fy = self._to_physical(from_coord)
        tx, ty = self._to_physical(to_coord)
        self._platform.mouse_drag(fx, fy, tx, ty, duration=duration)
        return {"status": "ok", "action": "drag",
                "from_physical": [fx, fy], "to_physical": [tx, ty]}

    def scroll(self, coord: NormalizedCoord, amount: int = 3,
               direction: str = "vertical") -> dict:
        """Scroll at normalized coordinate."""
        x, y = self._to_physical(coord)
        self._platform.mouse_scroll(x, y, amount=amount, direction=direction)
        return {"status": "ok", "action": "scroll", "amount": amount}


class HiddenKeyboard:
    """Low-level keyboard operations."""

    def __init__(self):
        self._platform = get_platform()

    def type(self, text: str) -> dict:
        """Type raw Unicode text."""
        self._platform.keyboard_type(text)
        return {"status": "ok", "action": "type", "length": len(text)}

    def press(self, keys: List[str]) -> dict:
        """Press a key combination."""
        self._platform.keyboard_press(keys)
        return {"status": "ok", "action": "press", "keys": keys}

    def key_down(self, key: str) -> dict:
        """Hold a key down."""
        self._platform.keyboard_key_down(key)
        return {"status": "ok", "action": "key_down", "key": key}

    def key_up(self, key: str) -> dict:
        """Release a held key."""
        self._platform.keyboard_key_up(key)
        return {"status": "ok", "action": "key_up", "key": key}


class HiddenScreenshot:
    """Screenshot capture — saves to user Downloads directory."""

    def __init__(self):
        self._platform = get_platform()
        self._output_dir = self._resolve_output_dir()

    @staticmethod
    def _resolve_output_dir() -> Path:
        """Use user's Downloads folder; fallback to cwd/screenshots."""
        import os
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        if os.path.isdir(downloads):
            return Path(downloads)
        return Path.cwd() / "screenshots"

    def capture(self, filename: Optional[str] = None) -> dict:
        """Capture full screen and save to Downloads."""
        import time, re
        self._output_dir.mkdir(parents=True, exist_ok=True)

        if filename is None:
            filename = f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
        else:
            # Sanitize: remove path separators, allow only safe chars
            filename = re.sub(r'[\\/:*?"<>|]', '_', filename)
            if not filename.endswith('.png'):
                filename += '.png'

        path = (self._output_dir / filename).resolve()
        # Ensure path stays within output_dir
        if not str(path).startswith(str(self._output_dir.resolve())):
            return {"status": "error", "error": "invalid filename"}
        img = self._platform.screenshot()
        img = self._platform.screenshot()
        img.save(str(path))

        return {"status": "ok", "action": "screenshot",
                "path": str(path), "size": path.stat().st_size}


class HiddenScreenRecorder:
    """Screen recording — prefers system-native recorder, outputs to Downloads."""

    def __init__(self):
        self._platform = get_platform()
        self._output_dir = HiddenScreenshot._resolve_output_dir()

    def start(self, filename: Optional[str] = None) -> dict:
        """Start screen recording.

        Args:
            filename: Optional filename (without extension).
                      Auto-generated if omitted.
        """
        import time
        self._output_dir.mkdir(parents=True, exist_ok=True)

        if filename is None:
            filename = f"recording_{time.strftime('%Y%m%d_%H%M%S')}"

        path = self._output_dir / f"{filename}.mp4"
        ok = self._platform.start_recording(path)

        return {
            "status": "ok" if ok else "error",
            "action": "start_recording",
            "path": str(path),
            "recording": ok,
        }

    def stop(self) -> dict:
        """Stop recording and return the file path."""
        result = self._platform.stop_recording()
        return {
            "status": "ok",
            "action": "stop_recording",
            "path": str(result) if result else None,
            "saved": result is not None,
        }
