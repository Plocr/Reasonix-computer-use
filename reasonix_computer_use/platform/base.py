"""
Platform abstraction layer — OS-agnostic interface for hardware simulation.

Every OS backend must implement PlatformProvider.  The Windows implementation
leverages Win32 APIs (SetCursorPos, SendInput, keybd_event) wrapped with
Per-Monitor DPI V2 awareness.

All coordinates accepted by these methods are PHYSICAL screen pixels.
Normalized → physical conversion happens BEFORE calling the platform layer.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from PIL.Image import Image


@dataclass
class WindowInfo:
    """Lightweight window descriptor returned by platform enumeration."""
    id: str                    # platform handle / window ID
    title: str
    process_name: str = ""
    process_id: int = 0
    rect: Tuple[int, int, int, int] = (0, 0, 0, 0)  # (left, top, right, bottom) physical px
    is_visible: bool = True
    is_minimized: bool = False
    dpi: int = 96
    scale_factor: float = 1.0


class PlatformProvider(abc.ABC):
    """Interface for OS-level hardware simulation.

    Every method receives PHYSICAL pixel coordinates.  The caller is
    responsible for converting from normalized spaces.
    """

    # ── Mouse ────────────────────────────────────────────────────────────

    @abc.abstractmethod
    def mouse_move(self, x: int, y: int) -> None:
        """Move cursor to absolute screen position (physical pixels)."""
        ...

    @abc.abstractmethod
    def mouse_click(self, x: int, y: int, button: str = "left",
                    count: int = 1, duration: float = 0.0) -> None:
        """Click at position.  count=2 for double-click."""
        ...

    @abc.abstractmethod
    def mouse_drag(self, from_x: int, from_y: int,
                   to_x: int, to_y: int, duration: float = 0.5) -> None:
        """Click-and-drag from one position to another."""
        ...

    @abc.abstractmethod
    def mouse_scroll(self, x: int, y: int, amount: int,
                     direction: str = "vertical") -> None:
        """Scroll at position.  amount positive = down/right."""
        ...

    # ── Keyboard ─────────────────────────────────────────────────────────

    @abc.abstractmethod
    def keyboard_type(self, text: str) -> None:
        """Type raw Unicode text at the current focus."""
        ...

    @abc.abstractmethod
    def keyboard_press(self, keys: List[str]) -> None:
        """Press a key combination (e.g. ["CTRL", "C"])."""
        ...

    @abc.abstractmethod
    def keyboard_key_down(self, key: str) -> None:
        """Hold a key down."""
        ...

    @abc.abstractmethod
    def keyboard_key_up(self, key: str) -> None:
        """Release a held key."""
        ...

    # ── Screen ───────────────────────────────────────────────────────────

    @abc.abstractmethod
    def screenshot(self, rect: Optional[Tuple[int, int, int, int]] = None) -> Image:
        """Capture a region of the screen in physical pixels.

        Args:
            rect: Optional (left, top, right, bottom).  None = full virtual screen.

        Returns:
            PIL Image in RGB mode.
        """
        ...

    @abc.abstractmethod
    def get_virtual_screen_rect(self) -> Tuple[int, int, int, int]:
        """Return (left, top, right, bottom) of the virtual desktop in physical px."""
        ...

    # ── Window ───────────────────────────────────────────────────────────

    @abc.abstractmethod
    def list_windows(self) -> List[WindowInfo]:
        """Enumerate visible top-level windows."""
        ...

    @abc.abstractmethod
    def get_window_rect(self, window_id: str) -> Tuple[int, int, int, int]:
        """Return (left, top, right, bottom) of a window in physical px."""
        ...

    @abc.abstractmethod
    def activate_window(self, window_id: str) -> bool:
        """Bring a window to the foreground.  Returns True on success."""
        ...

    @abc.abstractmethod
    def get_foreground_window(self) -> Optional[WindowInfo]:
        """Return the currently focused window."""
        ...

    # ── Recording ────────────────────────────────────────────────────────

    @abc.abstractmethod
    def start_recording(self, output_path: Path,
                        rect: Optional[Tuple[int, int, int, int]] = None) -> bool:
        """Start screen recording.  Prefer system-native recorder.

        Returns True if recording started successfully.
        """
        ...

    @abc.abstractmethod
    def stop_recording(self) -> Optional[Path]:
        """Stop recording and return the path to the saved file."""
        ...
