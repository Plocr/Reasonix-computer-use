# Reasonix Computer Use — Perception layer
# Precision-first, vision-fallback observation pipeline.

from .base import PerceptionProvider
from .router import PerceptionRouter
from .precision.windows_uia import WindowsUIAPrecision
from .precision.macos_axapi import MacOSAXAPIPrecision
from .precision.linux_atspi import LinuxATSPI2Precision
from .vision.easy_ocr import EasyOCRVision

__all__ = [
    "PerceptionProvider",
    "PerceptionRouter",
    "WindowsUIAPrecision",
    "MacOSAXAPIPrecision",
    "LinuxATSPI2Precision",
    "EasyOCRVision",
]
