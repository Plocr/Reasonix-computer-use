"""
macOS AXAPI precision provider — stub.

Full implementation requires PyObjC and the macOS Accessibility API (AXAPI).
This stub is a placeholder that reports unavailability on non-macOS systems.
"""

from __future__ import annotations

import sys
from typing import Optional

from ..base import PerceptionProvider
from ...protocol import ScreenSnapshot


class MacOSAXAPIPrecision(PerceptionProvider):
    """macOS precision layer via AXAPI (Accessibility API).

    Status: stub — not yet implemented.
    """

    @property
    def source(self) -> str:
        return "precision"

    @property
    def available(self) -> bool:
        """Only available on macOS with PyObjC installed."""
        if sys.platform != "darwin":
            return False
        try:
            import Quartz  # noqa: F401
            return True
        except ImportError:
            return False

    def observe(
        self,
        window_id: Optional[str] = None,
        max_elements: int = 80,
    ) -> ScreenSnapshot:
        raise NotImplementedError(
            "macOS AXAPI precision provider is not yet implemented. "
            "Contributions welcome — see E:/Agent/reasonix-computer-use/CONTRIBUTING.md"
        )
