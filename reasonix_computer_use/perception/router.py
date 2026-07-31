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
import threading
import time
from typing import Optional

from .base import PerceptionProvider
from .precision.windows_uia import WindowsUIAPrecision
from .precision.macos_axapi import MacOSAXAPIPrecision
from .precision.linux_atspi import LinuxATSPI2Precision
from ..protocol import ScreenSnapshot

logger = logging.getLogger(__name__)

# Blacklist TTL: retry precision after this many seconds
_BLACKLIST_TTL_SECONDS = 60


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

        # Failure tracking for fallback decisions (thread-safe)
        self._lock = threading.Lock()
        self._consecutive_failures: dict[str, int] = {}
        # Windows where precision returned empty — blacklisted with expiry
        self._precision_blacklist: dict[str, float] = {}  # window_key → expiry timestamp

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

    def _is_blacklisted(self, window_key: str) -> bool:
        """Check if a window is blacklisted (with TTL expiry)."""
        with self._lock:
            expiry = self._precision_blacklist.get(window_key)
            if expiry is None:
                return False
            if time.monotonic() >= expiry:
                # Expired — remove and allow retry
                del self._precision_blacklist[window_key]
                return False
            return True

    def _blacklist(self, window_key: str) -> None:
        """Add a window to the precision blacklist with TTL."""
        with self._lock:
            self._precision_blacklist[window_key] = time.monotonic() + _BLACKLIST_TTL_SECONDS

    def _resolve_foreground_key(self) -> str:
        """Resolve the foreground window to a stable key (cross-platform)."""
        from ..platform import get_platform
        try:
            plat = get_platform()
            fg = plat.get_foreground_window()
            if fg and fg.id:
                return str(fg.id)
        except Exception:
            pass
        return "foreground"

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

        # Resolve "foreground" to actual window handle for consistent blacklisting
        if window_key == "foreground":
            window_key = self._resolve_foreground_key()

        # ── Skip precision if this window was previously blacklisted ─────
        if self._is_blacklisted(window_key):
            force_vision = True

        # ── Try precision first (if not blacklisted) ─────────────────────
        if not force_vision:
            precision = self._best_precision()
            if precision is not None:
                try:
                    snapshot = precision.observe(window_id, max_elements)
                    if snapshot.elements:
                        with self._lock:
                            self._consecutive_failures[window_key] = 0
                        return snapshot
                    # Precision returned empty — blacklist this window (temporary)
                    logger.debug("Precision returned empty for '%s', blacklisting for %ds",
                                 window_key, _BLACKLIST_TTL_SECONDS)
                    self._blacklist(window_key)
                except Exception as exc:
                    logger.debug("Precision layer failed: %s", exc)

        # Track consecutive precision failures for adaptive fallback
        with self._lock:
            failures = self._consecutive_failures.get(window_key, 0) + 1
            self._consecutive_failures[window_key] = failures
        self._blacklist(window_key)
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
        with self._lock:
            self._consecutive_failures.clear()
            self._precision_blacklist.clear()
