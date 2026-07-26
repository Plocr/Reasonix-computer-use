"""
Linux AT-SPI2 precision provider — stub.

Full implementation requires the AT-SPI2 D-Bus interface (pyatspi or similar).
Wayland sessions are detected and automatically cause fallback to vision layer
because global coordinate APIs do not work under Wayland.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from ..base import PerceptionProvider
from ...protocol import ScreenSnapshot


def _is_wayland() -> bool:
    """Detect if running under a Wayland session."""
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


class LinuxATSPI2Precision(PerceptionProvider):
    """Linux precision layer via AT-SPI2 (D-Bus accessibility).

    Status: stub — not yet implemented.
    Automatically reports unavailable on Wayland (global coords broken).
    """

    @property
    def source(self) -> str:
        return "precision"

    @property
    def available(self) -> bool:
        """Available on Linux with X11 and pyatspi installed.

        Wayland sessions are excluded because global coordinate APIs are unavailable.
        """
        if sys.platform != "linux":
            return False
        if _is_wayland():
            return False
        try:
            import gi  # noqa: F401
            return True
        except ImportError:
            return False

    def observe(
        self,
        window_id: Optional[str] = None,
        max_elements: int = 80,
    ) -> ScreenSnapshot:
        raise NotImplementedError(
            "Linux AT-SPI2 precision provider is not yet implemented. "
            "Contributions welcome — see E:/Agent/reasonix-computer-use/CONTRIBUTING.md"
        )
