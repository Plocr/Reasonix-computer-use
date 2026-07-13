"""Local OCR fallback for custom-drawn Windows interfaces."""

from __future__ import annotations

import time
import threading

from reasonix_computer_use.mcp_server import register_tool
from reasonix_computer_use.screenshot import _capture_window
from reasonix_computer_use.utils import parse_result, tool_error
from reasonix_computer_use.windows import user32

_ocr_engine = None
_ocr_lock = threading.Lock()
_ocr_warming = False


def _ocr():
    global _ocr_engine
    if _ocr_engine is not None:
        return _ocr_engine
    with _ocr_lock:
        if _ocr_engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as exc:
                raise RuntimeError("本地 OCR 组件不可用；请安装完整发行包或开发依赖") from exc
            _ocr_engine = RapidOCR()
    return _ocr_engine


def prewarm_ocr() -> bool:
    """Load ONNX models in the persistent MCP process without delaying startup."""
    global _ocr_warming
    if _ocr_engine is not None or _ocr_warming:
        return False
    _ocr_warming = True

    def warm() -> None:
        global _ocr_warming
        try:
            _ocr()
        except Exception:
            pass
        finally:
            _ocr_warming = False

    threading.Thread(target=warm, name="reasonix-ocr-warmup", daemon=True).start()
    return True


def scan_text(window_id: str, max_results: int = 100) -> dict:
    import numpy as np

    image, info = _capture_window(window_id, activate=True)
    if user32.GetForegroundWindow() != info.hwnd:
        raise RuntimeError("目标窗口未获得前台焦点，已拒绝对遮挡区域执行 OCR")
    result, _ = _ocr()(np.asarray(image))
    matches = []
    for box, recognized, confidence in result or []:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        rect = [int(info.rect[0] + min(xs)), int(info.rect[1] + min(ys)),
                int(info.rect[0] + max(xs)), int(info.rect[1] + max(ys))]
        matches.append({"text": recognized, "confidence": round(float(confidence), 3), "rect": rect})
    matches.sort(key=lambda item: (item["rect"][1], item["rect"][0]))
    return {"status": "ok", "window": {"hwnd": hex(info.hwnd), "title": info.title},
            "matches": matches[:max(1, min(max_results, 200))]}


def find_text(window_id: str, text: str, exact: bool = False, max_results: int = 5) -> dict:
    scanned = scan_text(window_id, 200)
    needle = "".join(text.split()).casefold()
    matches = []
    for item in scanned["matches"]:
        normalized = "".join(str(item["text"]).split()).casefold()
        if normalized == needle if exact else needle in normalized:
            matches.append(item)
    matches.sort(key=lambda item: (-item["confidence"], item["rect"][1], item["rect"][0]))
    return {"status": "ok", "window": scanned["window"], "query": text,
            "matches": matches[:max(1, min(max_results, 20))]}


@register_tool(
    name="computer_find_text",
    description="UIA 无法定位时，使用本地中文 OCR 在目标窗口查找文字；不调用视觉模型。",
    schema={"type": "object", "properties": {
        "window_id": {"type": "string"}, "text": {"type": "string"},
        "exact": {"type": "boolean", "default": False},
        "max_results": {"type": "integer", "default": 5}},
        "required": ["window_id", "text"]})
async def computer_find_text(args: dict) -> str:
    try:
        return parse_result(find_text(args["window_id"], args["text"], args.get("exact", False),
                                      args.get("max_results", 5)))
    except Exception as exc:
        return tool_error("ocr_failed", str(exc), retryable=False,
                          fallback="最后才使用标注截图")


@register_tool(
    name="computer_click_text",
    description="UIA 无法操作时，用本地 OCR 查找并点击目标窗口中的文字；一次调用完成定位和点击。",
    schema={"type": "object", "properties": {
        "window_id": {"type": "string"}, "text": {"type": "string"},
        "exact": {"type": "boolean", "default": False},
        "index": {"type": "integer", "default": 0}},
        "required": ["window_id", "text"]})
async def computer_click_text(args: dict) -> str:
    try:
        result = find_text(args["window_id"], args["text"], args.get("exact", False), 20)
        index = args.get("index", 0)
        if not result["matches"] or index < 0 or index >= len(result["matches"]):
            return tool_error("text_not_found", f"目标窗口中没有找到文字：{args['text']}",
                              retryable=True, fallback="刷新窗口后重试一次，再使用标注截图")
        match = result["matches"][index]
        left, top, right, bottom = match["rect"]
        x, y = (left + right) // 2, (top + bottom) // 2
        if not user32.SetCursorPos(x, y):
            raise OSError("移动鼠标失败")
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.03)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        return parse_result({"status": "ok", "action": "click_text", "text": match["text"],
                             "confidence": match["confidence"], "point": [x, y], "rect": match["rect"]})
    except Exception as exc:
        return tool_error("ocr_click_failed", str(exc), retryable=False,
                          fallback="最后才使用标注截图")
