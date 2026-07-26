"""
EasyOCR + OpenCV vision provider.

Text recognition via EasyOCR (GPU accelerated, Chinese + English).
UI component detection via OpenCV (Canny edge + contour analysis).
Returns structured ElementRef list with pixel coordinates.

This provider does NOT make decisions — it only returns coordinates and text.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

import numpy as np
import cv2

from ..base import PerceptionProvider
from ...protocol import ScreenSnapshot, ElementRef

logger = logging.getLogger(__name__)

_EASYOCR_AVAILABLE = False
_IMPORT_ERROR: Optional[str] = None


def _easyocr_available() -> bool:
    """Re-check EasyOCR availability."""
    global _EASYOCR_AVAILABLE, _IMPORT_ERROR
    try:
        import easyocr  # noqa: F401
        _EASYOCR_AVAILABLE = True
        _IMPORT_ERROR = None
        return True
    except ImportError as e:
        _EASYOCR_AVAILABLE = False
        _IMPORT_ERROR = str(e)
        logger.debug("EasyOCR not available: %s", e)
        return False


# ── Singleton reader ────────────────────────────────────────────────────────

_reader = None
_last_annotated: Optional[str] = None  # Path to last annotated screenshot


def cleanup_annotations():
    """Delete all annotated screenshots from previous tasks."""
    import os, glob
    try:
        tmp_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "memory", "screenshots")
        for f in glob.glob(os.path.join(tmp_dir, "annotated_*.png")):
            os.remove(f)
            logger.debug("Cleaned annotation: %s", f)
    except Exception:
        pass


def _get_reader():
    """Lazy-init EasyOCR reader (GPU, Chinese + English)."""
    global _reader
    if not _easyocr_available():
        raise RuntimeError(f"EasyOCR not installed: {_IMPORT_ERROR}")
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(['ch_sim', 'en'], gpu=True, verbose=False)
    return _reader


# ── UI component detection (OpenCV) ─────────────────────────────────────────

def detect_ui_components(image_bgr: np.ndarray) -> List[dict]:
    """Detect UI components via Canny edge + contour analysis.

    Returns list of {'x': int, 'y': int, 'w': int, 'h': int}.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 30, 100)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
    dilated = cv2.dilate(closed, np.ones((2, 2), np.uint8), iterations=1)
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    img_area = image_bgr.shape[0] * image_bgr.shape[1]
    ui_boxes = []

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        if area < 200:          # 过滤噪点
            continue
        if area > img_area * 0.5:  # 过滤大背景
            continue
        aspect = w / max(h, 1)
        if aspect > 10 or aspect < 0.1:  # 过滤极端长宽比
            continue
        ui_boxes.append({'x': int(x), 'y': int(y), 'w': int(w), 'h': int(h)})

    return ui_boxes


# ── EasyOCR text detection ──────────────────────────────────────────────────

def detect_text(image_bgr: np.ndarray) -> List[dict]:
    """Run EasyOCR text recognition.

    Returns list of {'x': int, 'y': int, 'w': int, 'h': int,
                     'text': str, 'conf': float}.
    """
    reader = _get_reader()
    results = reader.readtext(image_bgr)

    text_blocks = []
    for (bbox, text, prob) in results:
        pts = np.array(bbox, dtype=np.int32)
        x_min = int(min(p[0] for p in pts))
        y_min = int(min(p[1] for p in pts))
        x_max = int(max(p[0] for p in pts))
        y_max = int(max(p[1] for p in pts))
        w = x_max - x_min
        h = y_max - y_min

        if w > 3 and h > 3 and prob > 0.5:
            text_blocks.append({
                'x': x_min, 'y': y_min, 'w': w, 'h': h,
                'text': str(text),
                'conf': round(float(prob) * 100, 1),
            })

    return text_blocks


# ── EasyOCRVision provider ──────────────────────────────────────────────────


def _draw_annotations(image_bgr: np.ndarray, text_blocks: List[dict],
                      ui_boxes: List[dict]) -> np.ndarray:
    """Draw blue boxes (text) and yellow boxes (UI components) on image."""
    result = image_bgr.copy()
    # Yellow boxes — UI components
    for box in ui_boxes:
        cv2.rectangle(result, (box['x'], box['y']),
                      (box['x'] + box['w'], box['y'] + box['h']), (0, 255, 255), 2)
    # Blue boxes — text
    for block in text_blocks:
        cv2.rectangle(result, (block['x'], block['y']),
                      (block['x'] + block['w'], block['y'] + block['h']), (255, 0, 0), 2)
    return result


class EasyOCRVision(PerceptionProvider):
    """Vision layer using EasyOCR + OpenCV UI detection.

    Returns ScreenSnapshot with:
      - Text elements (EasyOCR, blue) — id prefix "eocr_t"
      - UI components (OpenCV, yellow) — id prefix "eocr_u"

    All coordinates are physical pixels from the screenshot.
    """

    def __init__(self):
        pass

    @property
    def source(self) -> str:
        return "vision"

    @property
    def available(self) -> bool:
        return _easyocr_available()

    def observe(
        self,
        window_id: Optional[str] = None,
        max_elements: int = 80,
    ) -> ScreenSnapshot:
        """Run EasyOCR + OpenCV on the current screen."""
        if not _easyocr_available():
            raise RuntimeError(f"EasyOCR not available: {_IMPORT_ERROR}")

        from ...platform import get_platform
        platform = get_platform()

        # Capture full screen
        img = platform.screenshot()
        img_bgr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
        h, w = img_bgr.shape[:2]

        start = time.time()

        # 1. UI component detection (yellow boxes)
        ui_boxes = detect_ui_components(img_bgr)

        # 2. Text recognition (blue boxes)
        text_blocks = detect_text(img_bgr)

        # 3. Draw annotations on a copy for debugging
        import tempfile, os, time as _time
        annotated = _draw_annotations(img_bgr.copy(), text_blocks, ui_boxes)
        tmp_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "memory", "screenshots")
        os.makedirs(tmp_dir, exist_ok=True)
        ts = _time.strftime("%Y%m%d_%H%M%S")
        img_name = f"annotated_{ts}.png"
        img_path = os.path.join(tmp_dir, img_name)
        cv2.imwrite(img_path, annotated)
        img_abs = os.path.abspath(img_path)
        # Store last annotated path for the observe response
        global _last_annotated
        _last_annotated = img_abs

        # 4. Combine into ElementRef list
        elements: List[ElementRef] = []
        idx = 0

        # Text elements first (more actionable)
        for block in text_blocks[:max_elements]:
            elements.append(ElementRef(
                id=f"eocr_t{idx}",
                text=block['text'],
                role="Text",
                bbox=(block['x'], block['y'],
                      block['x'] + block['w'], block['y'] + block['h']),
            ))
            idx += 1

        # UI components
        for box in ui_boxes[:max_elements]:
            elements.append(ElementRef(
                id=f"eocr_u{idx}",
                text="",
                role="Button",
                bbox=(box['x'], box['y'],
                      box['x'] + box['w'], box['y'] + box['h']),
            ))
            idx += 1

        elapsed = time.time() - start
        logger.info(
            "EasyOCR: %d text + %d UI elements in %.2fs",
            len(text_blocks), len(ui_boxes), elapsed,
        )

        return ScreenSnapshot(
            revision=0,
            window_id=str(window_id or "screen"),
            source="vision",
            elements=elements[:max_elements],
            width=w,
            height=h,
            scale_factor=1.0,
        )
