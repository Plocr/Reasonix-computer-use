"""
Backward-compatibility shim for text_vision → perception.vision.easy_ocr.

This module exists so legacy code (domain_tools.py, old tests) that imports
from reasonix_computer_use.text_vision continues to work during the
transition to the new perception architecture.
"""

from __future__ import annotations


def _get_vision_provider():
    """Lazy-load the available vision provider."""
    from .perception.vision.easy_ocr import EasyOCRVision
    return EasyOCRVision()


def _image_to_bgr(image):
    """Convert a PIL image (or pass through a numpy BGR array)."""
    import cv2
    import numpy as np
    from PIL import Image
    if isinstance(image, Image.Image):
        return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    if isinstance(image, np.ndarray):
        return image
    raise TypeError(f"unsupported image type: {type(image).__name__}")


def _ocr_elements(image=None):
    """Return OCR element dicts for the given image (or the live screen).

    When an image is supplied it is OCR'd directly — the argument is never
    silently ignored (a previous revision fell back to a full-screen
    observe even when the caller passed a cropped region).
    """
    if image is not None:
        from .perception.vision.easy_ocr import detect_text
        blocks = detect_text(_image_to_bgr(image))
        return [{"text": block["text"],
                 "rect": [block["x"], block["y"],
                          block["x"] + block["w"], block["y"] + block["h"]],
                 "confidence": block["conf"] / 100.0} for block in blocks]
    provider = _get_vision_provider()
    snapshot = provider.observe()
    return [{"text": el.text, "rect": list(el.bbox), "confidence": 0.9}
            for el in snapshot.elements]


def find_text(image, target: str, **kwargs):
    """Legacy compatibility wrapper.  Use PerceptionRouter instead."""
    matches = []
    lowered_target = target.casefold()
    for item in _ocr_elements(image):
        if lowered_target in str(item.get("text", "")).casefold():
            rect = item["rect"]
            matches.append({
                "text": item["text"],
                "rect": rect,
                "center": [(rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2],
                "confidence": item.get("confidence", 0.9),
            })
    return matches


def scan_text(image, *args, **kwargs):
    """Legacy compatibility wrapper. Extra positional args silently accepted."""
    return _ocr_elements(image)


def scan_text_windows(image, **kwargs):
    """Legacy compatibility wrapper. Same as scan_text."""
    return scan_text(image, **kwargs)
