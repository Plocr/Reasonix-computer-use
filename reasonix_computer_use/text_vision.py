"""
Backward-compatibility shim for text_vision → perception.vision.paddle_ocr.

This module exists so legacy code (domain_tools.py, old tests) that imports
from reasonix_computer_use.text_vision continues to work during the
transition to the new perception architecture.
"""

from __future__ import annotations




def find_text(image, target: str, **kwargs):
    """Legacy compatibility wrapper.  Use PerceptionRouter instead."""
    from .perception.vision.paddle_ocr import PaddleOCRVision
    provider = PaddleOCRVision(enable_prewarm=False)
    snapshot = provider.observe_image(image)
    matches = []
    lowered_target = target.casefold()
    for el in snapshot.elements:
        if lowered_target in el.text.casefold():
            cx = (el.bbox[0] + el.bbox[2]) // 2
            cy = (el.bbox[1] + el.bbox[3]) // 2
            matches.append({
                "text": el.text,
                "rect": list(el.bbox),
                "center": [cx, cy],
                "confidence": 0.9,
            })
    return matches


def scan_text(image, *args, **kwargs):
    """Legacy compatibility wrapper. Extra positional args silently accepted."""
    from .perception.vision.paddle_ocr import PaddleOCRVision
    provider = PaddleOCRVision(enable_prewarm=False)
    snapshot = provider.observe_image(image)
    results = []
    for el in snapshot.elements:
        results.append({
            "text": el.text,
            "rect": list(el.bbox),
            "confidence": 0.9,
        })
    return results


def scan_text_windows(image, **kwargs):
    """Legacy compatibility wrapper. Same as scan_text."""
    return scan_text(image, **kwargs)
