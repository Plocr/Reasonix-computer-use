"""
Windows UIA (UI Automation) precision provider.

Uses comtypes to access the native Windows UIAutomation API.
Returns structured ScreenSnapshot with stable element IDs, bounding boxes,
text, and a11y roles.  No visual model involved — pure accessibility tree.
"""

from __future__ import annotations

import hashlib
import itertools
import time
from typing import Any, Dict, List, Optional, Tuple

from ..base import PerceptionProvider
from ...protocol import ScreenSnapshot, ElementRef

try:
    import comtypes
    import comtypes.client
    _UIA_AVAILABLE = True
except ImportError:
    comtypes = None
    _UIA_AVAILABLE = False


# ── UIA Control Type mapping ────────────────────────────────────────────────

CONTROL_TYPES: Dict[int, str] = {
    50000: "Window", 50001: "Button", 50003: "CheckBox", 50004: "ComboBox",
    50005: "Edit", 50006: "Hyperlink", 50007: "Image", 50008: "ListItem",
    50009: "List", 50010: "Menu", 50011: "MenuBar", 50012: "MenuItem",
    50013: "ProgressBar", 50014: "RadioButton", 50015: "ScrollBar",
    50016: "Slider", 50017: "Spinner", 50018: "StatusBar", 50019: "Tab",
    50020: "TabItem", 50021: "Text", 50022: "ToolBar", 50023: "ToolTip",
    50024: "Tree", 50025: "TreeItem", 50026: "Custom", 50027: "Group",
    50028: "Thumb", 50029: "DataGrid", 50030: "DataItem", 50031: "Document",
    50032: "Pane", 50033: "Header", 50034: "HeaderItem", 50035: "Table",
    50036: "TitleBar", 50037: "Separator",
}

INTERACTIVE_ROLES = {
    "Button", "CheckBox", "ComboBox", "Edit", "Hyperlink", "ListItem",
    "MenuItem", "RadioButton", "ScrollBar", "Slider", "Spinner", "TabItem",
    "TreeItem", "DataItem", "Document",
}


# ── Singleton UIA instance ──────────────────────────────────────────────────

_uia_instance = None


def _get_uia():
    global _uia_instance
    if not _UIA_AVAILABLE:
        raise RuntimeError("comtypes is not installed")
    if _uia_instance is None:
        comtypes.client.GetModule("UIAutomationCore.dll")
        constants = comtypes.gen.UIAutomationClient
        _uia_instance = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=constants.IUIAutomation,
        )
    return _uia_instance


# ── Element helpers ─────────────────────────────────────────────────────────

def _safe(element, name: str, default=None):
    try:
        value = getattr(element, name)
        return default if value is None else value
    except Exception:
        return default


def _cached_or_current(element, name: str, default=None):
    value = _safe(element, f"Cached{name}", None)
    if value is not None:
        return value
    return _safe(element, f"Current{name}", default)


def _properties(element, prefer_cached: bool = True) -> dict:
    """Extract UIA element properties into a dict."""
    read = (_cached_or_current if prefer_cached else
            lambda value, name, default=None: _safe(value, f"Current{name}", default))

    bounds = read(element, "BoundingRectangle")
    rect = None
    if bounds:
        try:
            rect = [int(bounds.left), int(bounds.top),
                    int(bounds.right), int(bounds.bottom)]
        except AttributeError:
            try:
                rect = [int(bounds[i]) for i in range(4)]
            except (TypeError, IndexError):
                rect = None

    role = CONTROL_TYPES.get(read(element, "ControlType", 0), "Unknown")

    result = {
        "name": read(element, "Name", ""),
        "automation_id": read(element, "AutomationId", ""),
        "class_name": read(element, "ClassName", ""),
        "role": role,
        "rect": rect,
        "enabled": bool(read(element, "IsEnabled", False)),
        "offscreen": bool(read(element, "IsOffscreen", True)),
        "focused": bool(read(element, "HasKeyboardFocus", False)),
    }

    # Value pattern (for Edit, ComboBox, Document)
    if role in ("Edit", "ComboBox", "Document"):
        try:
            constants = comtypes.gen.UIAutomationClient
            pattern = element.GetCurrentPattern(constants.UIA_ValuePatternId)
            value_pattern = pattern.QueryInterface(constants.IUIAutomationValuePattern)
            result["value"] = str(value_pattern.CurrentValue)
        except Exception:
            pass

    # Selection state
    if role in ("ListItem", "TreeItem", "TabItem", "DataItem"):
        try:
            constants = comtypes.gen.UIAutomationClient
            pattern = element.GetCurrentPattern(constants.UIA_SelectionItemPatternId)
            selection = pattern.QueryInterface(constants.IUIAutomationSelectionItemPattern)
            result["selected"] = bool(selection.CurrentIsSelected)
        except Exception:
            pass

    return result


def _is_meaningful(item: dict, scope: str = "interactive") -> bool:
    """Filter: only return elements useful for interaction."""
    rect = item.get("rect")
    if item["offscreen"] or not item["enabled"] or not rect:
        return False
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        return False
    if scope == "interactive":
        return item["role"] in INTERACTIVE_ROLES
    return bool(item["name"] or item["automation_id"] or item["role"] in INTERACTIVE_ROLES)


def _compact(item: dict, ref_id: str) -> dict:
    """Reduce an element dict to its essential fields."""
    result = {"ref": ref_id, "role": item["role"], "name": item["name"],
              "rect": item["rect"]}
    if item.get("automation_id"):
        result["id"] = item["automation_id"]
    if item.get("class_name"):
        result["class"] = item["class_name"]
    if item.get("value") not in (None, ""):
        result["value"] = item["value"]
    if item.get("focused"):
        result["focused"] = True
    if "selected" in item:
        result["selected"] = bool(item["selected"])
    return result


# ── Tree walker ─────────────────────────────────────────────────────────────

def _walk(element, max_depth: int = 15):
    """BFS walk of the UIA tree, yielding (element, depth)."""
    constants = comtypes.gen.UIAutomationClient
    automation = _get_uia()

    try:
        condition = automation.CreatePropertyCondition(
            constants.UIA_IsControlElementPropertyId, True
        )
    except (AttributeError, OSError):
        condition = automation.CreateTrueCondition()

    yield element, 0

    try:
        request = automation.CreateCacheRequest()
        for property_id in (
            constants.UIA_BoundingRectanglePropertyId,
            constants.UIA_ControlTypePropertyId,
            constants.UIA_NamePropertyId,
            constants.UIA_AutomationIdPropertyId,
            constants.UIA_ClassNamePropertyId,
            constants.UIA_IsEnabledPropertyId,
            constants.UIA_IsOffscreenPropertyId,
        ):
            request.AddProperty(property_id)
        descendants = element.FindAllBuildCache(
            constants.TreeScope_Descendants, condition, request
        )
    except Exception:
        try:
            descendants = element.FindAll(constants.TreeScope_Descendants, condition)
        except Exception:
            return

    try:
        for i in range(descendants.Length):
            yield descendants.GetElement(i), 1
    except Exception:
        return


# ── Window resolution ───────────────────────────────────────────────────────

def _resolve_hwnd(window_id: Optional[str] = None) -> int:
    """Resolve a window_id to an HWND integer."""
    import ctypes
    user32 = ctypes.windll.user32

    if not window_id:
        return user32.GetForegroundWindow()

    value = str(window_id).strip()
    # Try as raw hwnd number
    if value.isdigit() or value.lower().startswith("0x"):
        return int(value, 0)

    # Try as title substring
    needle = value.casefold()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.casefold()
        if needle in title:
            nonlocal result
            result = hwnd
            return False  # Stop enumeration
        return True

    result = 0
    user32.EnumWindows(callback, 0)
    if result:
        return result

    raise ValueError(f"Window not found: {window_id}")


# ── WindowsUIAPrecision ─────────────────────────────────────────────────────

class WindowsUIAPrecision(PerceptionProvider):
    """Precision layer via Windows UI Automation (UIA).

    Returns ScreenSnapshot with ElementRef items discovered from the
    accessibility tree.  No visual model — purely native a11y API.
    """

    def __init__(self):
        self._revision_counter: Dict[int, int] = {}
        self._ref_counter = itertools.count(1)

    @property
    def source(self) -> str:
        return "precision"

    @property
    def available(self) -> bool:
        return _UIA_AVAILABLE

    def observe(
        self,
        window_id: Optional[str] = None,
        max_elements: int = 80,
    ) -> ScreenSnapshot:
        if not _UIA_AVAILABLE:
            raise RuntimeError("UIA not available: comtypes is not installed")

        hwnd = _resolve_hwnd(window_id)
        root = _get_uia().ElementFromHandle(hwnd)

        # Get window dimensions for the snapshot
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)
        )

        elements: List[ElementRef] = []
        seen: set = set()
        limit = max(1, min(int(max_elements), 200))

        for element, _depth in _walk(root):
            item = _properties(element)
            if not _is_meaningful(item, "interactive"):
                continue

            dedupe = (item["role"], item["name"], tuple(item["rect"]))
            if dedupe in seen:
                continue
            seen.add(dedupe)

            ref_id = f"e{next(self._ref_counter)}"
            bbox = item["rect"] or [0, 0, 0, 0]

            el = ElementRef(
                id=ref_id,
                text=item.get("name", ""),
                role=item["role"],
                bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
            )
            elements.append(el)

            if len(elements) >= limit:
                break

        # Increment revision
        self._revision_counter[hwnd] = self._revision_counter.get(hwnd, 0) + 1

        return ScreenSnapshot(
            revision=self._revision_counter[hwnd],
            window_id=str(hwnd),
            source="precision",
            elements=elements,
            width=rect.right - rect.left,
            height=rect.bottom - rect.top,
            scale_factor=1.0,  # Will be enriched by caller with system-index data
        )
