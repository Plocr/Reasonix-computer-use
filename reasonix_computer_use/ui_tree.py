"""Compact Windows UI Automation observation and semantic actions."""

from __future__ import annotations

import hashlib
import itertools
import secrets
import time
import ctypes
from dataclasses import dataclass
from typing import Any

from reasonix_computer_use.mcp_server import register_tool
from reasonix_computer_use.utils import parse_result, tool_error
from reasonix_computer_use.windows import resolve_window, user32

try:
    import comtypes
    import comtypes.client
    _UIA_AVAILABLE = True
except ImportError:
    comtypes = None
    _UIA_AVAILABLE = False


CONTROL_TYPES = {
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
INTERACTIVE = {
    "Button", "CheckBox", "ComboBox", "Edit", "Hyperlink", "ListItem",
    "MenuItem", "RadioButton", "ScrollBar", "Slider", "Spinner", "TabItem",
    "TreeItem", "DataItem", "Document",
}
ACTION_MAP = {
    "Button": ["invoke"], "Hyperlink": ["invoke"], "MenuItem": ["invoke"],
    "Edit": ["set_value", "focus"], "Document": ["set_value", "focus"],
    "CheckBox": ["toggle"], "RadioButton": ["select"], "ListItem": ["select"],
    "TreeItem": ["select", "expand", "collapse"], "DataItem": ["select"],
    "ComboBox": ["focus", "click", "set_value", "expand", "collapse"], "TabItem": ["select"],
}


@dataclass
class ElementRef:
    ref: str
    hwnd: int
    element: Any
    selector: dict
    created: float


_refs: dict[str, ElementRef] = {}
_snapshots: dict[str, dict[str, dict]] = {}
_revisions: dict[int, int] = {}
_ref_counter = itertools.count(1)
_uia_instance = None
_observe_failures: dict[str, int] = {}
_visual_tokens: dict[str, tuple[str, float]] = {}


def _uia():
    global _uia_instance
    if not _UIA_AVAILABLE:
        raise RuntimeError("comtypes is not installed")
    if _uia_instance is None:
        # Supplying the interface is required; without it comtypes returns a
        # raw IUnknown whose ElementFromHandle method is unavailable.
        comtypes.client.GetModule("UIAutomationCore.dll")
        constants = comtypes.gen.UIAutomationClient
        _uia_instance = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=constants.IUIAutomation,
        )
    return _uia_instance


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
    read = (_cached_or_current if prefer_cached else
            lambda value, name, default=None: _safe(value, f"Current{name}", default))
    bounds = read(element, "BoundingRectangle")
    rect = None
    if bounds:
        try:
            rect = [int(bounds.left), int(bounds.top), int(bounds.right), int(bounds.bottom)]
        except AttributeError:
            try:
                rect = [int(bounds[index]) for index in range(4)]
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
    if role in ("ListItem", "TreeItem", "TabItem", "DataItem"):
        try:
            constants = comtypes.gen.UIAutomationClient
            pattern = element.GetCurrentPattern(constants.UIA_SelectionItemPatternId)
            selection = pattern.QueryInterface(constants.IUIAutomationSelectionItemPattern)
            result["selected"] = bool(selection.CurrentIsSelected)
        except Exception:
            pass
    if role in ("Edit", "ComboBox", "Document"):
        try:
            constants = comtypes.gen.UIAutomationClient
            pattern = element.GetCurrentPattern(constants.UIA_ValuePatternId)
            value_pattern = pattern.QueryInterface(constants.IUIAutomationValuePattern)
            result["value"] = str(value_pattern.CurrentValue)
        except Exception:
            pass
    return result


def _meaningful(item: dict, scope: str) -> bool:
    rect = item.get("rect")
    if item["offscreen"] or not item["enabled"] or not rect:
        return False
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        return False
    if scope == "interactive":
        return item["role"] in INTERACTIVE
    return bool(item["name"] or item["automation_id"] or item["role"] in INTERACTIVE)


def _signature(item: dict) -> str:
    raw = "|".join((item.get("automation_id", ""), item.get("name", ""),
                    item.get("role", ""), item.get("class_name", ""),
                    ",".join(map(str, item.get("rect") or []))))
    return hashlib.blake2s(raw.encode("utf-8"), digest_size=8).hexdigest()


def _compact(item: dict, ref: str | None = None) -> dict:
    result = {"ref": ref, "role": item["role"], "name": item["name"],
              "rect": item["rect"], "actions": ACTION_MAP.get(item["role"], ["click"])}
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
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def _walk(element, max_depth: int = 15):
    constants = comtypes.gen.UIAutomationClient
    automation = _uia()
    try:
        condition = automation.CreatePropertyCondition(constants.UIA_IsControlElementPropertyId, True)
    except (AttributeError, OSError):
        condition = automation.CreateTrueCondition()
    yield element, 0
    try:
        request = automation.CreateCacheRequest()
        for property_id in (
            constants.UIA_BoundingRectanglePropertyId, constants.UIA_ControlTypePropertyId,
            constants.UIA_NamePropertyId, constants.UIA_AutomationIdPropertyId,
            constants.UIA_ClassNamePropertyId, constants.UIA_IsEnabledPropertyId,
            constants.UIA_IsOffscreenPropertyId,
        ):
            request.AddProperty(property_id)
        descendants = element.FindAllBuildCache(constants.TreeScope_Descendants, condition, request)
    except Exception:
        try:
            descendants = element.FindAll(constants.TreeScope_Descendants, condition)
        except Exception:
            return
    try:
        for index in range(descendants.Length):
            yield descendants.GetElement(index), 1
    except Exception:
        return


def observe(window_id=None, scope="interactive", max_elements=80) -> dict:
    info = resolve_window(window_id)
    root = _uia().ElementFromHandle(info.hwnd)
    items: list[dict] = []
    seen: set[tuple] = set()
    for element, _depth in _walk(root):
        item = _properties(element)
        if not _meaningful(item, scope):
            continue
        dedupe = (item["role"], item["name"], tuple(item["rect"]))
        if dedupe in seen:
            continue
        seen.add(dedupe)
        ref = f"e{next(_ref_counter)}"
        selector = {"automation_id": item["automation_id"], "name": item["name"],
                    "role": item["role"], "class_name": item["class_name"]}
        _refs[ref] = ElementRef(ref, info.hwnd, element, selector, time.monotonic())
        compact = _compact(item, ref)
        compact["_sig"] = _signature(item)
        items.append(compact)
        if len(items) >= max(1, min(int(max_elements), 200)):
            break
    _revisions[info.hwnd] = _revisions.get(info.hwnd, 0) + 1
    revision = f"w{info.hwnd:x}-r{_revisions[info.hwnd]}"
    snapshot = {item["_sig"]: {k: v for k, v in item.items() if k != "_sig"} for item in items}
    _snapshots[revision] = snapshot
    for item in items:
        item.pop("_sig", None)
    _prune_caches()
    return {"status": "ok", "revision": revision,
            "window": {"hwnd": hex(info.hwnd), "title": info.title}, "elements": items}


def _prune_caches():
    now = time.monotonic()
    for key in list(_refs):
        if now - _refs[key].created > 300:
            del _refs[key]
    while len(_snapshots) > 20:
        del _snapshots[next(iter(_snapshots))]


@register_tool(
    name="computer_observe",
    description="以紧凑结构观察已打开的前台或指定窗口。打开应用时先调用应用列表和启动工具；禁止起手截图。",
    schema={"type": "object", "properties": {
        "window_id": {"type": "string"},
        "scope": {"type": "string", "enum": ["interactive", "all"], "default": "interactive"},
        "max_elements": {"type": "integer", "default": 80, "minimum": 1, "maximum": 200},
        "changed_since": {"type": "string"},
        "visual_fallback": {"type": "boolean", "default": True}}})
async def computer_observe(args: dict) -> str:
    target = str(args.get("window_id") or "foreground")
    try:
        from reasonix_computer_use.system_index import ensure_index
        ensure_index("first computer use")
        result = observe(args.get("window_id"), args.get("scope", "interactive"),
                         args.get("max_elements", 80))
        if not result["elements"]:
            raise RuntimeError("目标窗口没有暴露可交互的 UIA 元素")
        _observe_failures.pop(target, None)
        old_revision = args.get("changed_since")
        if old_revision:
            old = _snapshots.get(old_revision)
            if old is None:
                return tool_error("unknown_revision", "Revision is no longer available", retryable=True)
            current = _snapshots[result["revision"]]
            def comparable(value):
                return {key: item for key, item in value.items() if key != "ref"}
            result["elements"] = [value for key, value in current.items()
                                  if key not in old or comparable(old[key]) != comparable(value)]
            result["removed"] = [value["ref"] for key, value in old.items() if key not in current]
        return parse_result(result)
    except Exception as exc:
        count = _observe_failures.get(target, 0) + 1
        _observe_failures[target] = count
        if count < 2 or not args.get("visual_fallback", True):
            return tool_error("observe_failed", f"UIA 观察失败（第 {count} 次）：{exc}", retryable=True,
                              fallback="优先调用 computer_click_text；仅当 OCR 也失败时才重试 UIA 获取截图令牌")
        token = secrets.token_urlsafe(12)
        _visual_tokens[token] = (target, time.monotonic() + 60)
        return parse_result({"status": "error", "code": "uia_fallback_ready",
                             "message": f"UIA 连续失败 {count} 次，可使用一次目标窗口标注截图",
                             "retryable": True, "fallback_token": token,
                             "fallback": "调用 computer_screenshot(mode='window', annotate=true, fallback_token=...)"})


def consume_visual_fallback(token: str | None, window_id: str | None) -> tuple[bool, str]:
    if not token or token not in _visual_tokens:
        return False, "必须先对同一窗口连续调用两次 computer_observe，并使用返回的一次性 fallback_token"
    target, expires = _visual_tokens.pop(token)
    if time.monotonic() > expires:
        return False, "fallback_token 已过期，请重新执行 UIA 观察"
    requested = str(window_id or "foreground")
    if target != requested:
        return False, "fallback_token 与目标窗口不匹配"
    return True, ""


def _resolve_ref(ref: str) -> ElementRef:
    record = _refs.get(ref)
    if not record or time.monotonic() - record.created > 300 or not user32.IsWindow(record.hwnd):
        raise KeyError(ref)
    return record


def _pattern(element, pattern_id: int):
    return element.GetCurrentPattern(pattern_id)


def _click(item: dict):
    rect = item.get("rect")
    if not rect:
        raise RuntimeError("Element has no clickable rectangle")
    x, y = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    if not user32.SetCursorPos(x, y):
        raise ctypes.WinError()
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


@register_tool(
    name="computer_act",
    description="对 computer_observe 返回的元素 ref 执行语义操作。优先使用 UIA Pattern，坐标点击仅作回退。",
    schema={"type": "object", "properties": {
        "ref": {"type": "string"},
        "action": {"type": "string", "enum": ["invoke", "set_value", "toggle", "select", "expand", "collapse", "focus", "click"]},
        "value": {"type": "string"}, "verify": {"type": "boolean", "default": True}},
        "required": ["ref", "action"]})
async def computer_act(args: dict) -> str:
    value_pattern = None
    try:
        record = _resolve_ref(args["ref"])
    except KeyError:
        return tool_error("stale_ref", "Element ref expired or its window closed", retryable=True,
                          fallback="call computer_observe again")
    action, element = args["action"], record.element
    constants = comtypes.gen.UIAutomationClient
    try:
        if action == "invoke":
            _pattern(element, constants.UIA_InvokePatternId).QueryInterface(constants.IUIAutomationInvokePattern).Invoke()
        elif action == "set_value":
            value_pattern = _pattern(element, constants.UIA_ValuePatternId).QueryInterface(constants.IUIAutomationValuePattern)
            value_pattern.SetValue(args.get("value", ""))
        elif action == "toggle":
            _pattern(element, constants.UIA_TogglePatternId).QueryInterface(constants.IUIAutomationTogglePattern).Toggle()
        elif action == "select":
            _pattern(element, constants.UIA_SelectionItemPatternId).QueryInterface(constants.IUIAutomationSelectionItemPattern).Select()
        elif action in ("expand", "collapse"):
            pattern = _pattern(element, constants.UIA_ExpandCollapsePatternId).QueryInterface(constants.IUIAutomationExpandCollapsePattern)
            pattern.Expand() if action == "expand" else pattern.Collapse()
        elif action == "focus":
            element.SetFocus()
        elif action == "click":
            _click(_properties(element, prefer_cached=False))
        else:
            return tool_error("invalid_action", f"Unsupported action: {action}")
    except Exception as exc:
        if action not in ("set_value", "expand", "collapse"):
            try:
                _click(_properties(element, prefer_cached=False))
            except Exception:
                return tool_error("action_failed", str(exc), retryable=True,
                                  fallback="computer_screenshot with annotate=true")
        else:
            return tool_error("action_failed", str(exc), retryable=True,
                              fallback="computer_screenshot with annotate=true")
    result = {"status": "ok", "ref": record.ref, "action": action}
    if args.get("verify", True):
        current = _properties(element, prefer_cached=False)
        result["verified"] = bool(current.get("enabled") and not current.get("offscreen"))
        if action == "set_value" and value_pattern is not None:
            try:
                result["verified"] = result["verified"] and value_pattern.CurrentValue == args.get("value", "")
            except Exception:
                result["verified"] = False
            if not result["verified"]:
                return tool_error("value_not_set", "UIA ValuePattern 未能验证输入内容", retryable=True,
                                  fallback="保持焦点并使用键盘输入")
        result["element"] = _compact(current, record.ref)
    return parse_result(result)


@register_tool(
    name="computer_verify",
    description="不截图，直接验证窗口、元素 ref 或 UI revision 是否发生预期变化。",
    schema={"type": "object", "properties": {
        "window_id": {"type": "string"}, "ref": {"type": "string"},
        "exists": {"type": "boolean", "default": True}, "changed_since": {"type": "string"}}})
async def computer_verify(args: dict) -> str:
    try:
        if args.get("ref"):
            record = _resolve_ref(args["ref"])
            item = _properties(record.element, prefer_cached=False)
            exists = bool(item.get("rect"))
            return parse_result({"status": "ok", "matched": exists == args.get("exists", True),
                                 "element": _compact(item, record.ref) if exists else None})
        result = observe(args.get("window_id"), "interactive", 80)
        previous = args.get("changed_since")
        if previous:
            old = _snapshots.get(previous)
            if old is None:
                return tool_error("unknown_revision", "Revision is no longer available", retryable=True)
            current = _snapshots[result["revision"]]
            changed = set(old) != set(current) or any(
                {k: v for k, v in old[key].items() if k != "ref"} !=
                {k: v for k, v in current[key].items() if k != "ref"}
                for key in set(old) & set(current))
        else:
            changed = True
        return parse_result({"status": "ok", "matched": changed,
                             "revision": result["revision"], "window": result["window"]})
    except Exception as exc:
        return tool_error("verify_failed", str(exc), retryable=True)


@register_tool(
    name="computer_ui_tree",
    description="兼容用 UIA 树查询。为节省 token，默认应使用 computer_observe。",
    schema={"type": "object", "properties": {
        "window_id": {"type": "string"}, "max_depth": {"type": "integer", "default": 10},
        "include_offscreen": {"type": "boolean", "default": False},
        "compact": {"type": "boolean", "default": False},
        "max_elements": {"type": "integer", "default": 200}}})
async def computer_ui_tree(args: dict) -> str:
    try:
        info = resolve_window(args.get("window_id")) if args.get("window_id") else resolve_window()
        root = _uia().ElementFromHandle(info.hwnd)
        elements = []
        limit = max(1, min(args.get("max_elements", 200), 1000))
        for element, depth in _walk(root, args.get("max_depth", 10)):
            item = _properties(element)
            if item["offscreen"] and not args.get("include_offscreen", False):
                continue
            if args.get("compact"):
                item = _compact(item)
            else:
                item = {"name": item["name"], "automation_id": item["automation_id"],
                        "class_name": item["class_name"], "control_type": item["role"],
                        "bounding_rect": item["rect"], "enabled": item["enabled"],
                        "offscreen": item["offscreen"], "depth": depth}
            elements.append(item)
            if len(elements) >= limit:
                break
        return parse_result({"status": "ok", "window_id": hex(info.hwnd), "elements": elements})
    except Exception as exc:
        return tool_error("ui_tree_failed", str(exc), retryable=True)


@register_tool(
    name="computer_find_element",
    description="兼容用窗口内 UIA 元素搜索。默认应使用 computer_observe 返回的 ref。",
    schema={"type": "object", "properties": {
        "window_id": {"type": "string"}, "criteria": {"type": "object"},
        "match_type": {"type": "string", "enum": ["exact", "partial"], "default": "partial"},
        "max_results": {"type": "integer", "default": 10}}, "required": ["criteria"]})
async def computer_find_element(args: dict) -> str:
    criteria = args.get("criteria", {})
    if isinstance(criteria, str):
        try:
            import json
            decoded = json.loads(criteria)
            criteria = decoded if isinstance(decoded, dict) else {"name": criteria}
        except json.JSONDecodeError:
            criteria = {"name": criteria}
    if not isinstance(criteria, dict):
        return tool_error("invalid_criteria", "criteria must be an object or element name")
    aliases = {"control_type": "role", "visible_text": "name", "automation_id": "automation_id",
               "name": "name", "class_name": "class_name"}
    if not any(criteria.get(key) for key in aliases):
        return tool_error("invalid_criteria", "No valid search criteria provided")
    try:
        info = resolve_window(args.get("window_id"))
        root = _uia().ElementFromHandle(info.hwnd)
        matches = []
        for element, _ in _walk(root):
            item = _properties(element)
            matched = True
            for source, target in aliases.items():
                wanted = criteria.get(source)
                if not wanted:
                    continue
                actual = str(item.get(target, "")).casefold()
                wanted = str(wanted).casefold()
                if (wanted != actual if args.get("match_type", "partial") == "exact" else wanted not in actual):
                    matched = False
                    break
            if matched and (args.get("include_offscreen") or not item["offscreen"]):
                matches.append({"name": item["name"], "automation_id": item["automation_id"],
                                "class_name": item["class_name"], "control_type": item["role"],
                                "bounding_rect": item["rect"], "offscreen": item["offscreen"]})
            if len(matches) >= args.get("max_results", 10):
                break
        return parse_result({"status": "ok", "matches": matches, "count": len(matches)})
    except Exception as exc:
        return tool_error("find_failed", str(exc), retryable=True)
