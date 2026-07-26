"""
Perception router — precision-first, vision-fallback strategy.

Policy:
  1. Try the precision layer first (UIA / AXAPI / AT-SPI2).
  2. If precision fails or is unavailable, fall back to vision (EasyOCR).
  3. If both are unavailable, report blocked=true so the host can escalate.

This router does NOT make decisions about what to click — it only observes.
The host Agent receives the ScreenSnapshot and issues ActionCommands.
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import PerceptionProvider
from .precision.windows_uia import WindowsUIAPrecision
from .precision.macos_axapi import MacOSAXAPIPrecision
from .precision.linux_atspi import LinuxATSPI2Precision
from ..protocol import ScreenSnapshot

logger = logging.getLogger(__name__)


class PerceptionRouter:
    """Select and execute the best available perception provider.

    Strategy: precision → vision → unavailable.
    """

    def __init__(self):
        # Precision providers (platform-specific)
        self._precision = WindowsUIAPrecision()
        self._precision_macos = MacOSAXAPIPrecision()
        self._precision_linux = LinuxATSPI2Precision()

        # Vision providers — prefer EasyOCR (GPU) fallback EasyOCR (CPU)
        from .vision.easy_ocr import EasyOCRVision
        self._vision_easy = EasyOCRVision()
        # self._vision_vl = PaddleVLVision()  # Optional, activated on demand

        # Failure tracking for fallback decisions
        self._consecutive_failures: dict[str, int] = {}
        # Windows where precision returned empty — skip precision forever
        self._precision_blacklist: set[str] = set()

    def _best_precision(self) -> Optional[PerceptionProvider]:
        """Return the precision provider for the current platform, if available."""
        import sys

        if sys.platform == "win32" and self._precision.available:
            return self._precision
        elif sys.platform == "darwin" and self._precision_macos.available:
            return self._precision_macos
        elif sys.platform == "linux" and self._precision_linux.available:
            return self._precision_linux
        return None

    def observe(
        self,
        window_id: Optional[str] = None,
        max_elements: int = 80,
        force_vision: bool = False,
    ) -> ScreenSnapshot:
        """Observe a window, preferring precision over vision.

        Args:
            window_id: Platform window ID. None = foreground.
            max_elements: Max elements to return.
            force_vision: Skip precision and go directly to OCR.

        Returns:
            ScreenSnapshot with structured element data.
        """
        window_key = str(window_id or "foreground")

        # Resolve "foreground" to actual HWND for consistent blacklisting
        if window_key == "foreground":
            import ctypes
            try:
                fg = ctypes.windll.user32.GetForegroundWindow()
                if fg:
                    window_key = str(fg)
            except:
                pass

        # ── Skip precision if this window was previously blacklisted ─────
        if window_key in self._precision_blacklist:
            force_vision = True

        # ── Try precision first (if not blacklisted) ─────────────────────
        if not force_vision:
            precision = self._best_precision()
            if precision is not None:
                try:
                    snapshot = precision.observe(window_id, max_elements)
                    if snapshot.elements:
                        self._consecutive_failures[window_key] = 0
                        return snapshot
                    # Precision returned empty — blacklist this window
                    logger.debug("Precision returned empty for '%s', blacklisting", window_key)
                    self._precision_blacklist.add(window_key)
                except Exception as exc:
                    logger.debug("Precision layer failed: %s", exc)

        # Track consecutive precision failures for adaptive fallback
        failures = self._consecutive_failures.get(window_key, 0) + 1
        self._consecutive_failures[window_key] = failures
        self._precision_blacklist.add(window_key)
        logger.info(
            "Precision layer failed %d time(s) for window '%s', falling back to vision",
            failures, window_key,
        )

        # ── Fall back to vision (EasyOCR preferred, EasyOCR fallback) ──
        best_vision = None
        if self._vision_easy.available:
            best_vision = self._vision_easy

        if best_vision is not None:
            try:
                snapshot = best_vision.observe(window_id, max_elements)
                return snapshot
            except Exception as exc:
                logger.warning("Vision layer also failed: %s", exc)

        # ── Both unavailable ─────────────────────────────────────────────
        return ScreenSnapshot(
            revision=-1,
            window_id=window_key,
            source="unavailable",
            blocked=True,
            blocked_reason=(
                "Neither precision nor vision layer is available. "
                "Install comtypes (Windows UIA) or EasyOCR (cross-platform vision)."
            ),
        )

    @property
    def precision_available(self) -> bool:
        return self._best_precision() is not None

    @property
    def vision_available(self) -> bool:
        return self._vision_easy.available

    def reset_failures(self) -> None:
        """Reset the consecutive failure counter and blacklist."""
        self._consecutive_failures.clear()
        self._precision_blacklist.clear()
