"""Reasonix-facing domain tools for Windows computer use."""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from . import __version__
from .environment_setup import environment_status, start_environment_setup, wait_environment_status
from .input_guard import reserve_text_input
from .keyboard import VK_MAP, _send_key, computer_keyboard_press, computer_keyboard_type, paste_unicode_text
from .mcp_server import register_tool
from .mouse import computer_mouse_click, computer_mouse_move, computer_mouse_scroll
from .process_broker import LaunchBrokerError, launch_via_system_broker, shell_execute
from .runtime import (REGISTRY, STRATEGIES, WindowContext, memory_candidates, remember_success,
                      semantic_state, window_payload)
from .screenshot import _capture_window, _get_screenshot_dir
from .system_index import (build_index, enrich_index, ensure_index, find_app, is_strong_app_match,
                           query_profile, search_apps)
from .trace import export_trace, list_traces, trace_dir
from .utils import parse_result, tool_error
from .windows import (DPI_AWARENESS, activate_window, get_window_info, list_windows, resolve_window,
                      user32, virtual_screen)


MAX_ELEMENTS = 80
DEFAULT_ELEMENTS = 40
MAX_BATCH = 5
OCR_MIN_CONFIDENCE = 0.65
SENSITIVE_WORDS = ("密码", "验证码", "支付", "付款", "购买", "删除", "卸载", "协议", "password", "captcha", "payment")


def _ok(**values: Any) -> str:
    return parse_result({"status": "ok", **values})


def _environment_gate() -> str | None:
    status = environment_status()
    if status.get("ready"):
        return None
    return parse_result({
        "status": "setup_required",
        "blocked": True,
        "missing": status.get("missing", []),
        "message": "Computer Use 运行依赖尚未安装",
        "recommended_tool": "computer_system",
        "recommended_args": {"operation": "setup", "params": {"confirmed": True}},
        "next_hint": "先告知用户将下载依赖并获得确认，再调用 setup；禁止继续启动应用",
    })


def _public_app(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in
            ("id", "name", "version", "publisher", "path", "launch_target", "launch_args", "source", "confidence", "sha256")
            if item.get(key) not in (None, "")}


def _find_app_window(app: dict[str, Any], pid: int = 0, timeout: float = 8.0,
                     stable_seconds: float = 0.75):
    deadline = time.monotonic() + timeout
    target = str(app.get("path", "")).casefold()
    name = str(app.get("name", "")).casefold()
    stem = Path(target).stem.casefold() if target else ""
    best = None
    stable_key: tuple[int, int] | None = None
    stable_since = 0.0
    while time.monotonic() < deadline:
        candidates = [item for item in list_windows()
                      if item.rect[0] > -30000 and item.rect[1] > -30000
                      and item.rect[2] - item.rect[0] >= 50 and item.rect[3] - item.rect[1] >= 30]
        exact = [item for item in candidates if target and item.process_path.casefold() == target]
        exact_title = [item for item in candidates if name and item.title.casefold() == name
                       and item.rect[0] > -30000 and item.rect[1] > -30000]
        process = [item for item in candidates if pid and not target.startswith("shell:") and item.pid == pid]
        executable = [item for item in candidates if stem and Path(item.process_path).stem.casefold() == stem]
        # A title can mention another application (for example an Edge tab named
        # "WPS"). Never use a partial title match as application identity.
        ranked = exact or process or executable or exact_title
        if ranked:
            current = max(ranked, key=lambda item: (item.rect[2] - item.rect[0]) * (item.rect[3] - item.rect[1]))
            key = (current.hwnd, current.pid)
            if key != stable_key:
                stable_key = key
                stable_since = time.monotonic()
            best = current
            if time.monotonic() - stable_since >= stable_seconds:
                break
        else:
            stable_key = None
            stable_since = 0.0
        time.sleep(0.15)
    return best if stable_key and time.monotonic() - stable_since >= stable_seconds else None


def _launch(app: dict[str, Any]) -> tuple[int, str]:
    target = str(app.get("launch_target") or app.get("path") or "")
    if target.casefold().startswith("shell:appsfolder\\"):
        shell_execute(target)
        return 0, "wmi-explorer"
    if not target or not os.path.isfile(target):
        raise FileNotFoundError(f"应用启动目标不存在：{target or app.get('name')}")
    args = str(app.get("launch_args") or "")
    return launch_via_system_broker(target, args, str(Path(target).parent))


def _prime_window_state(context: WindowContext, info) -> None:
    """Initialize window context state after launch (visual-only, no UIA warmup)."""
    context.update({"title": info.title, "rect": info.rect, "hwnd": info.hwnd,
                    "pid": info.pid, "path": info.process_path}, "window")


@register_tool(
    name="computer_app",
    description="查找、启动、聚焦或关闭 Windows 应用。启动默认直接传 query；app_id 只能使用搜索结果。关闭优先复用启动响应的 window_id。禁止 Shell 搜索。",
    schema={"type": "object", "properties": {
        "operation": {"type": "string", "enum": ["search", "launch", "open_file", "focus", "list_running", "close"]},
        "query": {"type": "string", "description": "应用名称。launch 时优先直接使用，例如 Notepad。"},
        "app_id": {"type": "string", "description": "仅使用 search 返回的 id；普通应用名应传 query。"},
        "window_id": {"type": "string", "description": "launch 返回的窗口 id；focus/close 应原样复用。"},
        "path": {"type": "string", "description": "open_file 使用的现有文件绝对路径。"},
        "close_mode": {"type": "string", "enum": ["window", "process"], "default": "window"},
        "confirmed": {"type": "boolean", "default": False},
        "limit": {"type": "integer", "default": 10}}, "required": ["operation"]})
async def computer_app(args: dict) -> str:
    operation = args.get("operation")
    try:
        gate = _environment_gate()
        if gate:
            return gate
        if operation == "search":
            query = str(args.get("query", "")).strip()
            if not query:
                return tool_error("missing_query", "搜索应用必须提供 query")
            matches = search_apps(query, args.get("limit", 10))
            return _ok(query=query, matches=[_public_app(item) for item in matches],
                       next_hint="选择 app_id 后调用 computer_app(operation=launch)" if matches else
                       "索引已增量刷新但仍未找到；请用户提供应用名称或安装位置")
        if operation == "list_running":
            return _ok(windows=REGISTRY.running(min(int(args.get("limit", 10)), 20)),
                       next_hint="使用 window_id 调用 computer_state")
        if operation == "open_file":
            file_path = Path(str(args.get("path", ""))).expanduser()
            if not file_path.is_absolute() or not file_path.is_file():
                return tool_error("file_not_found", "open_file 必须提供存在的文件绝对路径")
            app = find_app(str(args.get("app_id", ""))) if args.get("app_id") else None
            if app is None and args.get("query"):
                matches = search_apps(str(args["query"]), 10)
                strong = [item for item in matches if is_strong_app_match(str(args["query"]), item)]
                app = strong[0] if strong else None
                if app is None:
                    return tool_error("ambiguous_app", "未找到可安全用于打开文件的精确应用")
            before_handles = {item.hwnd for item in list_windows()}
            pid = 0
            if app:
                target = str(app.get("launch_target") or app.get("path") or "")
                if not target or not os.path.isfile(target):
                    return tool_error("stale_app_path", "指定应用启动路径无效")
                pid, launch_method = launch_via_system_broker(
                    target, subprocess.list2cmdline([str(file_path)]), str(Path(target).parent))
            else:
                shell_execute(str(file_path))
                launch_method = "wmi-explorer"
            deadline = time.monotonic() + 8
            info = None
            stable_key: tuple[int, int] | None = None
            stable_since = 0.0
            while time.monotonic() < deadline:
                candidates = list_windows()
                named = [item for item in candidates if file_path.stem.casefold() in item.title.casefold()]
                created = [item for item in candidates if item.hwnd not in before_handles]
                owned = [item for item in candidates if pid and item.pid == pid]
                ranked = named or owned or created
                if ranked:
                    current = max(ranked, key=lambda item: (item.rect[2] - item.rect[0]) *
                                                         (item.rect[3] - item.rect[1]))
                    key = (current.hwnd, current.pid)
                    if key != stable_key:
                        stable_key = key
                        stable_since = time.monotonic()
                    info = current
                    if time.monotonic() - stable_since >= 0.75:
                        break
                else:
                    stable_key = None
                    stable_since = 0.0
                time.sleep(0.15)
            if not info or time.monotonic() - stable_since < 0.75:
                return tool_error("window_not_found", "文件已交给系统打开，但未发现对应窗口", retryable=True)
            context = REGISTRY.register(info, app)
            context.begin_task()
            _prime_window_state(context, info)
            return _ok(file=str(file_path), app=_public_app(app or {}),
                       window=window_payload(context, info), launch_method=launch_method,
                       detached=True, next_hint="调用 computer_state")
        if operation == "launch":
            app = find_app(str(args.get("app_id", ""))) if args.get("app_id") else None
            normalized_query = ""
            if app is None and args.get("app_id") and not args.get("query"):
                # Be forgiving when a model puts an application name in app_id.
                normalized_query = str(args["app_id"])
                matches = search_apps(normalized_query, 10)
                strong = [item for item in matches if is_strong_app_match(normalized_query, item)]
                app = strong[0] if strong else None
            if app is None and args.get("query"):
                matches = search_apps(str(args["query"]), 10)
                if not matches:
                    return tool_error("app_not_found", f"系统索引中没有找到应用：{args['query']}",
                                      fallback="请用户确认应用名称或安装位置")
                launchable = [item for item in matches if str(item.get("launch_target") or item.get("path") or "")]
                exact = [item for item in launchable if str(item.get("name", "")).casefold() == str(args["query"]).casefold()]
                if exact:
                    app = exact[0]
                else:
                    strong = [item for item in launchable if is_strong_app_match(str(args["query"]), item)]
                    if not strong:
                        return parse_result({"status": "error", "code": "ambiguous_app",
                                             "message": f"没有可安全启动的精确应用：{args['query']}",
                                             "matches": [_public_app(item) for item in launchable[:5]],
                                             "retryable": False,
                                             "next_hint": "从候选中选择 app_id；目录位置请使用 computer_system(profile)"})
                    app = strong[0]
            if app is None:
                return tool_error("app_not_found", "必须提供有效 app_id 或 query")
            existing = _find_app_window(app, timeout=0.3, stable_seconds=0.0)
            if existing:
                context = REGISTRY.register(existing, app)
                context.begin_task()
                try:
                    activate_window(existing.hwnd)
                except OSError:
                    pass
                _prime_window_state(context, existing)
                return _ok(app=_public_app(app), window=window_payload(context, existing), reused=True,
                           next_hint="调用 computer_state 获取当前窗口状态")
            try:
                pid, launch_method = _launch(app)
            except FileNotFoundError:
                build_index(f"stale-launch-target:{app.get('name')}")
                refreshed = find_app(str(app.get("id", "")))
                if not refreshed:
                    return tool_error("stale_app_path", "应用路径已失效，索引已刷新", retryable=True)
                app = refreshed
                pid, launch_method = _launch(app)
            info = _find_app_window(app, pid)
            if not info:
                return tool_error("window_not_found", "应用已启动，但等待 8 秒后仍未发现窗口", retryable=True,
                                  fallback="调用 computer_app(list_running) 查看新窗口")
            context = REGISTRY.register(info, app)
            context.begin_task()
            _prime_window_state(context, info)
            payload = {"app": _public_app(app), "window": window_payload(context, info),
                       "launch_method": launch_method, "detached": True,
                       "next_hint": "调用 computer_state 获取与目标相关的 UIA/OCR 元素"}
            if normalized_query:
                payload["normalized_from"] = {"app_id": normalized_query}
            return _ok(**payload)
        if args.get("window_id"):
            context = REGISTRY.get(str(args["window_id"]))
        else:
            context = REGISTRY.find(str(args.get("app_id", "")), str(args.get("query", "")))
        info = context.info()
        if operation == "focus":
            activate_window(info.hwnd)
            return _ok(window=window_payload(context, info), next_hint="调用 computer_state")
        if operation == "close":
            mode = args.get("close_mode", "window")
            if mode == "process":
                if not args.get("confirmed"):
                    return tool_error("confirmation_required", "结束目标应用后台进程需要 confirmed=true")
                handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, info.pid)
                if not handle:
                    raise ctypes.WinError()
                try:
                    if not ctypes.windll.kernel32.TerminateProcess(handle, 0):
                        raise ctypes.WinError()
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
                return _ok(closed="process", pid=info.pid, app=context.app_name or info.title)
            user32.PostMessageW(info.hwnd, 0x0010, 0, 0)
            return _ok(closed="window", app=context.app_name or info.title)
        return tool_error("invalid_operation", f"不支持的应用操作：{operation}")
    except LaunchBrokerError as exc:
        return parse_result({"status": "error", "code": "launch_isolation_failed",
                             "message": str(exc), "retryable": False, "blocked": True,
                             "next_hint": "停止重复启动；WMI 系统代理不可用，需用户检查 Windows Management Instrumentation 服务"})
    except KeyError:
        return parse_result({"status": "error", "code": "unknown_window", "retryable": False,
                             "blocked": True,
                             "message": "window_id 无法恢复；旧式窗口 ID 可能来自已重启的 MCP 进程",
                             "next_hint": "停止重复 launch；调用一次 computer_app(list_running) 获取稳定窗口 ID，仍无目标窗口则请求用户介入"})
    except Exception as exc:
        return tool_error("app_operation_failed", str(exc), retryable=True)


def _bounded_element(item: dict[str, Any]) -> dict[str, Any]:
    allowed = ("ref", "role", "name", "value", "rect", "actions", "id", "class",
               "action", "focused", "selected", "coordinate_space")
    result = {key: item.get(key) for key in allowed if item.get(key) not in (None, "", [])}
    for key, maximum in (("name", 120), ("value", 120), ("id", 80), ("class", 80)):
        if key in result:
            value = str(result[key])
            result[key] = value if len(value) <= maximum else value[:maximum - 3] + "..."
    return result


def _image_digest(image) -> str:
    small = image.convert("L").resize((32, 32))
    return hashlib.sha1(small.tobytes()).hexdigest()


@register_tool(
    name="computer_state",
    description="观察一个 Windows 窗口。先查应用记忆，再返回当前窗口截图供视觉模型理解。Agent 基于截图识别元素并决定操作。",
    schema={"type": "object", "properties": {
        "window_id": {"type": "string"}, "goal": {"type": "string"},
        "since_revision": {"type": "string"}},
        "required": ["window_id", "goal"]})
async def computer_state(args: dict) -> str:
    """纯视觉感知：截图 + 视觉模型理解。Agent 看截图后测量元素位置。"""
    try:
        gate = _environment_gate()
        if gate:
            return gate
        context = REGISTRY.get(str(args["window_id"]))
        if context.hard_blocked:
            return _ok(window=window_payload(context), revision=context.revision, source="none", elements=[],
                       progress="连续无效动作已触发执行熔断", blocked=True,
                       next_hint="停止工具调用并向用户报告当前阻断；新任务或重新启动应用后再继续")
        info = context.info()
        goal = str(args.get("goal", ""))
        memory = memory_candidates(context, goal)
        if memory:
            candidates = [_bounded_element(item.get("action", {})) for item in memory[:5]]
            return _ok(window=window_payload(context, info), revision=context.revision, source="memory",
                       elements=[], memory_hits=len(memory), progress="已命中该应用的验证成功路径",
                       next_hint="优先使用记忆中的路径调用 computer_action；否则截图观察")
        image, current = _capture_window(hex(info.hwnd), activate=False)
        digest = _image_digest(image)
        if digest == context.image_hash and context.visual_sent_for_revision == context.revision:
            return _ok(window=window_payload(context, current), revision=context.revision, source="visual",
                       unchanged=True, progress="当前窗口与上一张截图相同",
                       next_hint="窗口未变化；执行新操作或请用户介入")
        path = Path(_get_screenshot_dir()) / f"state_{context.window_id}_r{context.revision_no + 1}-{digest[:6]}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "PNG")
        context.image_hash = digest
        context.source = "visual"
        context.revision_no += 1
        context.revision = f"r{context.revision_no}-{digest[:6]}"
        context.visual_sent_for_revision = context.revision
        context.elements = []
        context.references.clear()
        left, top, right, bottom = current.rect
        return _ok(window=window_payload(context, current), revision=context.revision, source="visual",
                   image_path=str(path.resolve()), origin=[left, top], size=[right - left, bottom - top],
                   coordinate_space="window", progress="已返回当前窗口截图",
                   next_hint="分析截图识别目标元素，使用 click_point（窗口内坐标）或 click_ref 执行动作")
    except KeyError:
        return parse_result({"status": "error", "code": "unknown_window", "retryable": False,
                             "blocked": True,
                             "message": "window_id 无法恢复；禁止重复启动同一应用",
                             "next_hint": "调用一次 computer_app(list_running) 获取稳定窗口 ID，仍未找到则停止并报告阻断"})
    except Exception as exc:
        return tool_error("state_failed", str(exc), retryable=True)


def _parse_result(value: str) -> dict[str, Any]:
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {"status": "error"}
    except json.JSONDecodeError:
        return {"status": "error", "message": value[:300]}


def _point(context: WindowContext, action: dict[str, Any]) -> tuple[int, int]:
    x, y = int(action["x"]), int(action["y"])
    left, top, right, bottom = context.info().rect
    coordinate_space = action.get("coordinate_space", "window")
    if action.get("relative") is False:
        coordinate_space = "screen"
    if coordinate_space == "window":
        x, y = left + x, top + y
    elif coordinate_space != "screen":
        raise ValueError("coordinate_space 必须是 window 或 screen")
    if not (left <= x < right and top <= y < bottom):
        raise ValueError("坐标不在当前窗口物理像素范围内")
    return x, y


def _element_for_ref(context: WindowContext, ref: str) -> dict[str, Any] | None:
    return next((item for item in context.elements if item.get("ref") == ref), None) or context.references.get(ref)


def _action_sensitive(context: WindowContext, action: dict[str, Any]) -> bool:
    if action.get("confirmed"):
        return False
    keys = ("name", "purpose") if action.get("type") == "type" else ("text", "name", "purpose")
    target = " ".join(str(action.get(key, "")) for key in keys).casefold()
    if any(word in target for word in SENSITIVE_WORDS):
        return True
    if action.get("type") == "click_ref":
        element = _element_for_ref(context, str(action.get("ref", ""))) or {}
        label = str(element.get("name", "")).casefold()
        return any(word in label for word in SENSITIVE_WORDS)
    return bool(action.get("sensitive"))


async def _click_ref(context: WindowContext, ref: str) -> dict[str, Any]:
    """Click a visual ref: look up its rect in references, convert to screen coords, click center."""
    element = _element_for_ref(context, ref)
    if not element:
        return {"status": "error", "code": "stale_ref", "message": "ref 不属于当前 revision，请重新调用 computer_state"}
    rect = element.get("rect", [])
    if len(rect) != 4:
        return {"status": "error", "code": "invalid_ref", "message": "ref 缺少有效矩形"}
    left, top, right, bottom = (int(value) for value in rect)
    x, y = _point(context, {"x": (left + right) // 2, "y": (top + bottom) // 2})
    return _parse_result(await computer_mouse_click({
        "x": x, "y": y, "button": "left"}))


def _activate_for_keyboard(context: WindowContext) -> bool:
    try:
        activate_window(context.hwnd)
    except OSError:
        pass
    return user32.GetForegroundWindow() == context.hwnd


def _press_parts(keys: Any) -> tuple[str, list[str]]:
    values = [keys] if isinstance(keys, str) else list(keys or [])
    if len(values) == 1 and "+" in str(values[0]):
        values = [part.strip() for part in str(values[0]).split("+") if part.strip()]
    if not values:
        return "", []
    return str(values[-1]), [str(value) for value in values[:-1]]


def _validate_shortcut(key: str, modifiers: list[str]) -> str:
    valid_modifiers = {"ctrl", "alt", "shift", "win", "meta"}
    invalid = [value for value in modifiers if value.casefold() not in valid_modifiers]
    if invalid:
        return f"未知修饰键：{', '.join(invalid)}"
    lowered = key.casefold()
    if lowered not in VK_MAP and not (len(key) == 1 and key.isprintable()):
        return f"未知按键：{key}"
    return ""


def _requires_observable_change(action: dict[str, Any]) -> bool:
    kind = action.get("type")
    if kind in ("click_ref", "click_text", "click_point", "double_click", "right_click",
                "middle_click", "drag", "scroll"):
        return True
    if kind == "press":
        key, modifiers = _press_parts(action.get("keys", []))
        combo = (key.casefold(), frozenset(value.casefold() for value in modifiers))
        return combo not in {("c", frozenset({"ctrl"})), ("s", frozenset({"ctrl"}))}
    return False


async def _select_all() -> dict[str, Any]:
    return _parse_result(await computer_keyboard_press({"key": "a", "modifiers": ["ctrl"]}))


def _active_office_application():
    try:
        import comtypes.client
    except ImportError:
        return None
    for progid in ("Excel.Application", "KET.Application", "ket.Application"):
        try:
            return comtypes.client.GetActiveObject(progid)
        except Exception:
            continue
    return None


def _office_selection_address() -> str:
    application = _active_office_application()
    if application is None:
        return ""
    try:
        selection = application.ActiveWindow.RangeSelection
        return str(selection.Address(False, False)).replace("$", "").upper()
    except Exception:
        return ""


async def _verify_spreadsheet_selection(context: WindowContext, target: str) -> bool:
    """Verify spreadsheet selection via Office COM (no UIA)."""
    if _office_selection_address() == target.upper():
        return True
    await asyncio.sleep(0.3)
    return _office_selection_address() == target.upper()


async def _save_as(context: WindowContext, action: dict[str, Any]) -> dict[str, Any]:
    destination = Path(str(action.get("path", ""))).expanduser()
    if not destination.is_absolute() or not destination.parent.is_dir():
        return {"status": "error", "code": "invalid_save_path",
                "message": "save_as 必须提供父目录已存在的绝对路径"}
    if destination.exists() and not action.get("confirmed"):
        return {"status": "error", "code": "confirmation_required",
                "message": "目标文件已存在，覆盖前需要 confirmed=true"}
    if context.info().class_name not in ("XLMAIN", "OpusApp"):
        return {"status": "error", "code": "unsupported_save_as",
                "message": "当前 save_as 仅支持 Excel 和 WPS 表格"}
    if not _activate_for_keyboard(context):
        return {"status": "error", "code": "focus_denied"}
    application = _active_office_application()
    if application is not None:
        try:
            application.ActiveWorkbook.SaveAs(str(destination))
            if destination.is_file():
                return {"status": "ok", "action": "save_as", "path": str(destination),
                        "verified": True, "method": "office-automation"}
        except Exception:
            pass
    opened = _parse_result(await computer_keyboard_press({"key": "f12", "modifiers": []}))
    if opened.get("status") != "ok":
        return opened
    await asyncio.sleep(0.5)
    foreground = user32.GetForegroundWindow()
    if foreground:
        try:
            info = get_window_info(foreground)
            if info.pid == context.owner_pid:
                context.hwnd = foreground
        except (ValueError, OSError):
            pass
    # Type the full path via keyboard (no UIA dialog handling).
    await computer_keyboard_press({"key": "a", "modifiers": ["ctrl"]})
    await asyncio.sleep(0.1)
    type_result = _parse_result(await computer_keyboard_type({
        "text": str(destination), "interval": 0.01}))
    if type_result.get("status") != "ok":
        return type_result
    submitted = _parse_result(await computer_keyboard_press({"key": "enter", "modifiers": []}))
    if submitted.get("status") != "ok":
        return submitted
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if destination.is_file():
            return {"status": "ok", "action": "save_as", "path": str(destination),
                    "verified": True, "method": "filesystem"}
        await asyncio.sleep(0.2)
    return {"status": "error", "code": "save_not_verified",
            "message": "已提交保存，但目标文件未出现"}


async def _execute(context: WindowContext, action: dict[str, Any]) -> dict[str, Any]:
    kind = action.get("type")
    if kind == "click_ref":
        return await _click_ref(context, str(action.get("ref", "")))
    if kind in ("click_point", "double_click", "right_click", "middle_click"):
        x, y = _point(context, action)
        button = {"right_click": "right", "middle_click": "middle"}.get(kind, "left")
        click_type = "double" if kind == "double_click" else "single"
        return _parse_result(await computer_mouse_click({"x": x, "y": y, "button": button, "click_type": click_type}))
    if kind in ("move", "hover"):
        x, y = _point(context, action)
        return _parse_result(await computer_mouse_move({"x": x, "y": y, "duration": action.get("duration", 0.1)}))
    if kind == "drag":
        start_x, start_y = _point(context, {"x": action["from_x"], "y": action["from_y"],
                                            "relative": action.get("relative", False)})
        end_x, end_y = _point(context, {"x": action["to_x"], "y": action["to_y"],
                                        "relative": action.get("relative", False)})
        user32.SetCursorPos(start_x, start_y)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        steps = max(2, min(int(action.get("steps", 10)), 30))
        for step in range(1, steps + 1):
            user32.SetCursorPos(start_x + (end_x - start_x) * step // steps,
                                start_y + (end_y - start_y) * step // steps)
            await asyncio.sleep(0.02)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        return {"status": "ok", "action": "drag"}
    if kind == "scroll":
        amount = int(action.get("amount", 3))
        direction = action.get("direction", "down" if amount >= 0 else "up")
        return _parse_result(await computer_mouse_scroll({"direction": direction, "lines": abs(amount)}))
    if kind in ("select_cell", "select_range"):
        target = str(action.get("cell") if kind == "select_cell" else action.get("range", "")).strip().upper()
        pattern = r"[A-Z]{1,3}[1-9][0-9]{0,6}" if kind == "select_cell" else r"[A-Z]{1,3}[1-9][0-9]{0,6}:[A-Z]{1,3}[1-9][0-9]{0,6}"
        if not re.fullmatch(pattern, target):
            return {"status": "error", "code": "invalid_selection",
                    "message": "cell 使用 A1 格式；range 使用 A1:A101 格式"}
        if not _activate_for_keyboard(context):
            return {"status": "error", "code": "focus_denied",
                    "message": "目标表格窗口不是前台窗口"}
        opened = _parse_result(await computer_keyboard_press({"key": "g", "modifiers": ["ctrl"]}))
        if opened.get("status") != "ok":
            return opened
        await asyncio.sleep(0.2)
        typed = _parse_result(await computer_keyboard_type({"text": target, "interval": 0.01}))
        if typed.get("status") != "ok":
            return typed
        submitted = _parse_result(await computer_keyboard_press({"key": "enter", "modifiers": []}))
        if submitted.get("status") != "ok":
            return submitted
        if not await _verify_spreadsheet_selection(context, target):
            return {"status": "error", "code": "selection_not_verified",
                    "message": f"已执行定位，但无法确认当前选区 {target}"}
        return {"status": "ok", "action": kind, "cell" if kind == "select_cell" else "range": target,
                "method": "spreadsheet_go_to", "selected": True}
    if kind == "save_as":
        return await _save_as(context, action)
    if kind == "type":
        if not _activate_for_keyboard(context):
            return {"status": "error", "code": "focus_denied",
                    "message": "目标窗口不是前台窗口，已拒绝键盘注入"}
        if action.get("replace", True):
            selected = await _select_all()
            if selected.get("status") != "ok":
                return selected
        return _parse_result(await computer_keyboard_type({"text": str(action.get("text", "")),
                                                            "interval": min(float(action.get("interval", 0.01)), 0.1)}))
    if kind == "press":
        key, modifiers = _press_parts(action.get("keys", []))
        if not key:
            return {"status": "error", "code": "missing_keys"}
        invalid = _validate_shortcut(key, modifiers)
        if invalid:
            return {"status": "error", "code": "invalid_shortcut", "message": invalid}
        if key.casefold() == "f5" and context.info().class_name in ("OpusApp", "XLMAIN"):
            return {"status": "error", "code": "spreadsheet_f5_blocked",
                    "message": "表格应用中禁止用 F5 猜测单元格位置；请使用 select_cell 或 select_range"}
        if not _activate_for_keyboard(context):
            return {"status": "error", "code": "focus_denied",
                    "message": "目标窗口不是前台窗口，已拒绝键盘注入"}
        return _parse_result(await computer_keyboard_press({"key": key, "modifiers": modifiers}))
    if kind in ("key_down", "key_up"):
        key = str(action.get("key", "")).casefold()
        if key not in VK_MAP:
            return {"status": "error", "code": "unknown_key"}
        _send_key(VK_MAP[key], key_up=kind == "key_up")
        return {"status": "ok", "action": kind}
    if kind == "wait":
        await asyncio.sleep(max(0, min(float(action.get("seconds", 0.5)), 10)))
        return {"status": "ok", "action": "wait"}
    return {"status": "error", "code": "unknown_action", "message": f"不支持的动作：{kind}"}


def _normalize_action(action: Any) -> dict[str, Any]:
    """Accept common pre-0.8 guesses, while keeping `type` canonical."""
    if not isinstance(action, dict):
        return {}
    normalized = dict(action)
    if not normalized.get("type") and normalized.get("action"):
        legacy = str(normalized["action"])
        if legacy in ("click", "click_ref") and normalized.get("ref"):
            normalized["type"] = "click_ref"
        elif legacy == "click" and "x" in normalized and "y" in normalized:
            normalized["type"] = "click_point"
        elif legacy in ("type", "press", "click_text"):
            normalized["type"] = legacy
    return normalized


def _verify_typed_text(context: WindowContext, action: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if result.get("method") == "uia_value":
        return {"verified": True, "method": "uia-value"}
    text = str(action.get("text", ""))
    if not text:
        return {"verified": True, "method": "empty-input"}
    try:
        matched = bool(find_text(hex(context.hwnd), text, False, 1).get("matches"))
        return {"verified": matched, "method": "ocr-text"}
    except Exception as exc:
        return {"verified": False, "method": "ocr-text", "error": str(exc)[:160]}


def _adopt_new_window(context: WindowContext, old_handles: set[int]) -> None:
    foreground = user32.GetForegroundWindow()
    if foreground and foreground not in old_handles:
        try:
            info = get_window_info(foreground)
            belongs = bool(context.app_path and info.process_path.casefold() == context.app_path.casefold())
            belongs = belongs or bool(context.owner_pid and info.pid == context.owner_pid)
            belongs = belongs or bool(context.app_name and context.app_name.casefold() == info.title.casefold())
            if belongs:
                context.hwnd = foreground
                return
        except (ValueError, OSError):
            pass
    if context.app_path:
        candidates = [item for item in list_windows()
                      if item.process_path.casefold() == context.app_path.casefold()
                      or (context.owner_pid and item.pid == context.owner_pid)]
        if candidates:
            best = max(candidates, key=lambda item: (item.rect[2] - item.rect[0]) * (item.rect[3] - item.rect[1]))
            context.hwnd = best.hwnd


def _refresh_semantic(context: WindowContext) -> bool:
    info = context.info()
    try:
        result = observe(hex(info.hwnd), "interactive", MAX_ELEMENTS)
        elements = _window_elements(info, result.get("elements", []))
        return context.update(semantic_state(info, elements), "uia", elements)
    except Exception:
        return context.update({"title": info.title, "rect": info.rect, "hwnd": info.hwnd}, context.source)


async def _wait_stable(context: WindowContext, timeout: float = 1.5) -> None:
    deadline = time.monotonic() + timeout
    previous = ""
    stable = 0
    while time.monotonic() < deadline:
        try:
            info = context.info()
            items = _window_elements(info, observe(
                hex(info.hwnd), "interactive", MAX_ELEMENTS).get("elements", []))
            digest = hashlib.sha1(json.dumps(semantic_state(info, items), ensure_ascii=False,
                                             sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            stable = stable + 1 if digest == previous else 0
            if stable >= 1:
                return
            previous = digest
        except Exception:
            await asyncio.sleep(0.2)
            return
        await asyncio.sleep(0.1)


def _verify(context: WindowContext, expect: dict[str, Any], changed: bool,
            requires_change: bool = False) -> dict[str, Any]:
    info = context.info()
    if expect.get("window_title_contains"):
        wanted = str(expect["window_title_contains"]).casefold()
        return {"verified": wanted in info.title.casefold(), "method": "window-title"}
    for key, desired in (("text_present", True), ("text_absent", False)):
        if expect.get(key):
            wanted = str(expect[key]).casefold()
            uia_match = any(wanted in str(item.get("name", "")).casefold() for item in context.elements)
            if uia_match is desired:
                return {"verified": True, "method": "uia-text"}
            try:
                ocr_match = bool(find_text(hex(info.hwnd), str(expect[key]), False, 1).get("matches"))
                return {"verified": ocr_match is desired, "method": "ocr-text"}
            except Exception as exc:
                return {"verified": False, "method": "ocr-text", "error": str(exc)[:200]}
    if requires_change and not changed:
        return {"verified": False, "method": "pixel-or-semantic-change", "changed": False}
    return {"verified": True, "method": "semantic-change" if changed else "action-result", "changed": changed}


@register_tool(
    name="computer_action",
    description='在最新 revision 上执行最多五步。actions 每项必须使用 type 字段，例如 {"type":"click_ref","ref":"e1"}、{"type":"type","text":"周杰伦"}、{"type":"press","keys":["ENTER"]}。视觉点是窗口内物理像素坐标。',
    schema={"type": "object", "properties": {
        "window_id": {"type": "string"}, "revision": {"type": "string"},
        "actions": {"type": "array", "minItems": 1, "maxItems": 5, "items": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "动作种类，不要使用 action 或 command 字段。", "enum": ["click_ref", "click_text", "click_point", "move", "hover", "double_click", "right_click", "middle_click", "drag", "scroll", "select_cell", "select_range", "save_as", "type", "press", "key_down", "key_up", "wait"]},
                "ref": {"type": "string"}, "text": {"type": "string"},
                "cell": {"type": "string", "description": "select_cell 使用的单元格地址，例如 A1、A101"},
                "range": {"type": "string", "description": "select_range 使用的范围，例如 A1:A101"},
                "path": {"type": "string", "description": "save_as 使用的目标绝对路径"},
                "x": {"type": "integer"}, "y": {"type": "integer"},
                "from_x": {"type": "integer"}, "from_y": {"type": "integer"},
                "to_x": {"type": "integer"}, "to_y": {"type": "integer"},
                "coordinate_space": {"type": "string", "enum": ["window", "screen"], "default": "window"},
                "keys": {"type": "array", "items": {"type": "string"}},
                "key": {"type": "string"}, "amount": {"type": "integer"},
                "direction": {"type": "string", "enum": ["up", "down"]},
                "seconds": {"type": "number"}, "duration": {"type": "number"},
                "interval": {"type": "number"}, "steps": {"type": "integer"},
                "replace": {"type": "boolean", "default": True},
                "exact": {"type": "boolean"},
                "index": {"type": "integer"}, "confirmed": {"type": "boolean"},
                "purpose": {"type": "string"}
            },
            "required": ["type"]
        }},
        "expect": {"type": "object", "properties": {
            "text_present": {"type": "string"}, "text_absent": {"type": "string"},
            "window_title_contains": {"type": "string"}
        }}}, "required": ["window_id", "revision", "actions"]})
async def computer_action(args: dict) -> str:
    try:
        gate = _environment_gate()
        if gate:
            return gate
        context = REGISTRY.get(str(args["window_id"]))
        if str(args.get("revision")) != context.revision:
            return tool_error("stale_revision", "窗口状态已变化；请调用 computer_state 获取最新 revision")
        actions = args.get("actions")
        if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_BATCH:
            return tool_error("invalid_batch", "actions 必须包含 1 到 5 个动作")
        actions = [_normalize_action(action) for action in actions]
        coordinate_actions = {"click_point", "move", "hover", "double_click", "right_click",
                              "middle_click", "drag"}
        if context.restored and any(action.get("type") in coordinate_actions for action in actions):
            if context.source != "visual" or not context.image_hash:
                return tool_error(
                    "restored_coordinate_requires_ref",
                    "MCP 已恢复结构化状态；OCR/UIA 目标必须使用 click_text 或 click_ref，不能复用坐标",
                    retryable=True,
                )
            current_hash = _image_digest(_capture_window(hex(context.hwnd), activate=False)[0])
            if current_hash != context.image_hash:
                return tool_error("stale_revision", "窗口图片已经变化；请重新调用 computer_state")
            context.restored = False
        supported = {"click_ref", "click_text", "click_point", "move", "hover", "double_click",
                     "right_click", "middle_click", "drag", "scroll", "type", "press", "key_down",
                     "key_up", "select_cell", "select_range", "save_as", "wait"}
        if any(action.get("type") not in supported for action in actions):
            blocked = context.invalid_action()
            return parse_result({"status": "error", "code": "invalid_action_shape", "blocked": blocked,
                                 "progress": "动作未执行",
                                 "message": "actions 每项必须使用 type；例如 {\"type\":\"click_ref\",\"ref\":\"e1\"}",
                                 "next_hint": "按示例修正一次；blocked=true 时停止并报告"})
        for action in actions:
            if _action_sensitive(context, action):
                return tool_error("confirmation_required", "检测到密码、验证码、支付、删除或协议操作，需要用户确认或接管")
        results = []
        before = context.state_hash
        memory_actions = []
        needs_pixel_verification = any(_requires_observable_change(action) for action in actions)
        before_pixel = ""
        if needs_pixel_verification:
            try:
                before_pixel = _image_digest(_capture_window(hex(context.hwnd), activate=False)[0])
            except Exception:
                pass
        old_handles = {item.hwnd for item in list_windows()}
        last_edit_ref = ""
        for index, action in enumerate(actions):
            if action.get("type") == "type" and last_edit_ref:
                action["_target_ref"] = last_edit_ref
            memory_action = dict(action)
            if action.get("type") == "click_ref":
                element = _element_for_ref(context, str(action.get("ref", "")))
                if element:
                    memory_action["text"] = element.get("name", "")
                    memory_action["selector"] = {key: element.get(key) for key in
                                                 ("name", "role", "automation_id", "class_name") if element.get(key)}
            memory_actions.append(memory_action)
            signature = json.dumps(action, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            scoped = f"{context.revision}:{signature}"
            if scoped in context.action_signatures:
                context.fail()
                return tool_error("repeat_blocked", "同一 revision 上的相同动作已经执行，禁止重复点击",
                                  fallback="调用 computer_state 升级到下一感知策略")
            context.action_signatures.add(scoped)
            if action.get("type") == "type":
                info = context.info()
                app_identity = context.app_fingerprint or context.app_path or context.app_id or context.app_name
                if not reserve_text_input(app_identity=app_identity, window_class=info.class_name,
                                          state_hash=context.state_hash,
                                          target_ref=str(action.get("_target_ref", "")),
                                          text=str(action.get("text", ""))):
                    context.fail()
                    return tool_error(
                        "input_replay_blocked",
                        "检测到旧任务正在重复同一文本输入，已在键盘注入前阻止",
                        fallback="停止旧任务；如确需再次输入，请等待十分钟并重新观察当前窗口",
                    )
            result = await _execute(context, action)
            safe_result = {key: value for key, value in result.items() if key not in ("text", "value", "clipboard")}
            results.append({"index": index, **safe_result})
            if result.get("status") != "ok":
                level = context.fail()
                return parse_result({"status": "error", "code": "batch_stopped", "results": results,
                                     "progress": "动作失败，后续动作未执行", "blocked": context.hard_blocked or level >= 3,
                                     "next_hint": f"重新观察并升级到 {STRATEGIES[level]}"})
            if action.get("type") == "click_ref":
                target = _element_for_ref(context, str(action.get("ref", ""))) or {}
                last_edit_ref = str(action.get("ref")) if target.get("role") in ("Edit", "Document", "ComboBox") else ""
                context.focused_ref = last_edit_ref
            elif action.get("type") not in ("wait",):
                if action.get("type") != "type":
                    last_edit_ref = ""
            _adopt_new_window(context, old_handles)
            await _wait_stable(context)
            if action.get("type") == "type":
                input_verification = _verify_typed_text(context, action, result)
                if not input_verification.get("verified") and result.get("method") == "send_input":
                    try:
                        paste_unicode_text(str(action.get("text", "")))
                        await _wait_stable(context)
                        input_verification = _verify_typed_text(
                            context, action, {"method": "clipboard_paste"})
                        if input_verification.get("verified"):
                            results[-1]["method"] = "clipboard_paste"
                            results[-1]["fallback_used"] = True
                    except Exception as exc:
                        input_verification = {"verified": False, "method": "clipboard-paste",
                                              "error": str(exc)[:160]}
                if not input_verification.get("verified"):
                    level = context.fail()
                    return parse_result({"status": "error", "code": "input_not_verified", "results": results,
                                         "verification": input_verification, "progress": "输入 API 已调用，但目标窗口未出现文字，后续动作已停止",
                                         "blocked": context.hard_blocked or level >= 3, "next_hint": "重新观察一次以确认焦点；不要重复盲目键入"})
        changed = _refresh_semantic(context)
        if needs_pixel_verification and before_pixel:
            try:
                after_pixel = _image_digest(_capture_window(hex(context.hwnd), activate=False)[0])
                changed = changed or after_pixel != before_pixel
            except Exception:
                pass
        verification = _verify(context, args.get("expect", {}), changed,
                               requires_change=needs_pixel_verification)
        if not verification.get("verified"):
            level = context.fail()
            return parse_result({"status": "error", "code": "verification_failed", "results": results,
                                 "verification": verification, "revision": context.revision,
                                 "progress": "动作已执行但预期状态未出现", "blocked": context.hard_blocked or level >= 3,
                                 "next_hint": f"调用 computer_state，监督器将升级到 {STRATEGIES[level]}"})
        for action in memory_actions:
            remember_success(context, action, before, context.state_hash)
        context.succeed()
        return _ok(window=window_payload(context), revision=context.revision, results=results,
                   verification=verification, progress="动作已完成并通过本地验证", blocked=False,
                   next_hint="目标已完成则停止；否则用最新 revision 继续 computer_state/computer_action")
    except KeyError:
        return tool_error("unknown_window", "window_id 不存在；请先调用 computer_app")
    except Exception as exc:
        return tool_error("action_failed", str(exc), retryable=True)


READ_ONLY_COMMANDS = ("where", "where.exe", "get-command", "get-process", "get-ciminstance",
                      "get-item", "get-childitem", "gci", "test-path", "resolve-path",
                      "dir", "ls", "type", "whoami", "systeminfo", "tasklist")
DANGEROUS_COMMANDS = ("remove-item", " del ", "erase ", "format ", "shutdown", "restart-computer", "stop-process",
                      "reg add", "reg delete", "invoke-expression", "iex ", "rm ")
GUI_SCRIPT_MARKERS = ("sendkeys", "sendwait", "mouse_event", "setcursorpos", "user32", "add-type",
                      "wscript.shell", "setforegroundwindow", "keybd_event", "sendinput")


def _safe_read_only_command(command: str) -> bool:
    lowered = command.casefold().strip()
    if not lowered or any(marker in command for marker in (";", "|", "&", ">", "<", "\n", "\r", "`", "$")):
        return False
    if any(marker in lowered for marker in ("-recurse", " /s", "-depth")):
        return False
    first = lowered.split(maxsplit=1)[0]
    return first in READ_ONLY_COMMANDS


def _file_operation(target: str, params: dict[str, Any]) -> dict[str, Any]:
    operation = str(params.get("action", "metadata"))
    path = Path(target)
    if operation == "metadata":
        stat = path.stat()
        return {"path": str(path.resolve()), "exists": True, "is_dir": path.is_dir(),
                "size": stat.st_size, "modified": stat.st_mtime}
    if not params.get("confirmed"):
        raise PermissionError("文件写入操作需要 confirmed=true")
    destination = Path(str(params.get("destination", "")))
    if operation == "copy":
        result = shutil.copy2(path, destination) if path.is_file() else shutil.copytree(path, destination)
    elif operation in ("move", "rename"):
        result = shutil.move(str(path), str(destination))
    elif operation == "mkdir":
        path.mkdir(parents=bool(params.get("parents", True)), exist_ok=bool(params.get("exist_ok", True)))
        result = str(path)
    else:
        raise ValueError(f"不支持的文件操作：{operation}")
    return {"action": operation, "result": str(result)}


def _window_operation(target: str, params: dict[str, Any]) -> dict[str, Any]:
    context = REGISTRY.get(target)
    info = context.info()
    action = params.get("action", "focus")
    if action == "focus":
        activate_window(info.hwnd)
    elif action == "minimize":
        user32.ShowWindow(info.hwnd, 6)
    elif action == "maximize":
        user32.ShowWindow(info.hwnd, 3)
    elif action == "restore":
        user32.ShowWindow(info.hwnd, 9)
    elif action in ("move", "resize"):
        left, top, right, bottom = info.rect
        x, y = int(params.get("x", left)), int(params.get("y", top))
        width, height = int(params.get("width", right - left)), int(params.get("height", bottom - top))
        if not user32.MoveWindow(info.hwnd, x, y, width, height, True):
            raise ctypes.WinError()
    else:
        raise ValueError(f"不支持的窗口操作：{action}")
    context.update({"title": context.info().title, "rect": context.info().rect}, "window")
    return window_payload(context)


@register_tool(
    name="computer_system",
    description="查询环境、首次安装依赖、执行诊断、受控文件操作和窗口管理。setup 后用 setup_status 获取精简进度；command 仅允许单条只读诊断。",
    schema={"type": "object", "properties": {
        "operation": {"type": "string", "enum": ["profile", "refresh", "diagnose", "setup", "setup_status", "trace", "file", "window", "command"]},
        "target": {"type": "string"}, "params": {"type": "object"}}, "required": ["operation"]})
async def computer_system(args: dict) -> str:
    operation = args.get("operation")
    target = str(args.get("target", ""))
    params = args.get("params") if isinstance(args.get("params"), dict) else {}
    try:
        if operation == "profile":
            return _ok(profile=query_profile(target), next_hint="应用启动请使用 computer_app，不要使用 Shell")
        if operation == "refresh":
            reason = str(params.get("reason", "manual-refresh"))
            index = build_index(reason)
            if params.get("enrich", False):
                index = enrich_index(f"{reason}:enriched")
            return _ok(updated_at=index["updated_at"], applications=len(index.get("applications", [])),
                       profile="system.md", index="system-index.json")
        if operation == "diagnose":
            index = ensure_index()
            return _ok(version=__version__, platform_supported=os.name == "nt",
                       dpi_awareness=DPI_AWARENESS, virtual_screen=virtual_screen(),
                       displays=index.get("displays", []),
                       uia_available=importlib.util.find_spec("comtypes") is not None,
                       ocr_available=importlib.util.find_spec("rapidocr_onnxruntime") is not None,
                       input_available=bool(user32), public_tools=4,
                       environment=environment_status())
        if operation == "setup":
            result = start_environment_setup(bool(params.get("confirmed", False)))
            return parse_result(result)
        if operation == "setup_status":
            wait_seconds = max(0.0, min(float(params.get("wait_seconds", 0)), 30.0))
            return parse_result(wait_environment_status(wait_seconds))
        if operation == "trace":
            action = str(params.get("action", "status")).casefold()
            if action == "status":
                traces = list_traces(50)
                return _ok(enabled=True, schema_version=1, retained=len(traces), limit=50,
                           screenshots=False, directory=str(trace_dir()))
            if action == "list":
                return _ok(traces=list_traces(int(params.get("limit", 20))))
            if action == "export":
                if not params.get("confirmed"):
                    return tool_error("confirmation_required", "导出脱敏 trace 需要 confirmed=true")
                trace_id = str(params.get("trace_id", ""))
                destination = str(params.get("destination", ""))
                if not trace_id or not destination:
                    return tool_error("missing_trace_export", "export 必须提供 trace_id 和 destination")
                try:
                    exported = export_trace(trace_id, destination)
                except FileNotFoundError:
                    return tool_error("trace_not_found", f"没有找到 trace：{trace_id}")
                except ValueError as exc:
                    return tool_error("invalid_trace_destination", str(exc))
                return _ok(trace_id=trace_id, exported=exported, redacted=True)
            return tool_error("invalid_trace_action", "trace action 必须是 status、list 或 export")
        if operation == "file":
            return _ok(result=_file_operation(target, params))
        if operation == "window":
            return _ok(window=_window_operation(target, params))
        if operation == "command":
            legacy_command = str(params.get("command", "")).strip()
            if legacy_command:
                return parse_result({"status": "error", "code": "command_argument_blocked", "blocked": True,
                                     "message": "command 只能通过 target 传入；params.command 已被拒绝以防绕过检查",
                                     "next_hint": "桌面操作只能使用 computer_state/computer_action"})
            command = target.strip()
            lowered = f" {command.casefold()} "
            if any(marker in lowered for marker in GUI_SCRIPT_MARKERS):
                return parse_result({"status": "error", "code": "gui_command_blocked", "blocked": True,
                                     "message": "禁止通过 Shell 绕过 Computer Use 执行 GUI 输入",
                                     "next_hint": "停止命令尝试；仅使用 computer_state/computer_action 或请求用户介入"})
            if any(word in lowered for word in DANGEROUS_COMMANDS):
                return tool_error("command_blocked", "命令包含删除、终止进程或系统修改")
            if not _safe_read_only_command(command):
                return parse_result({"status": "error", "code": "command_not_read_only", "blocked": True,
                                     "message": "Alpha 版本只允许无管道、无变量展开的单条只读诊断命令",
                                     "next_hint": "停止 Shell 尝试；系统写入使用专用操作并由用户确认"})
            completed = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                       capture_output=True, timeout=min(float(params.get("timeout", 15)), 30),
                                       text=True, encoding="utf-8", errors="replace",
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            stdout, stderr = completed.stdout[:4000], completed.stderr[:1000]
            return _ok(returncode=completed.returncode, stdout=stdout, stderr=stderr,
                       truncated=len(completed.stdout) > len(stdout) or len(completed.stderr) > len(stderr))
        return tool_error("invalid_operation", f"不支持的系统操作：{operation}")
    except PermissionError as exc:
        return tool_error("confirmation_required", str(exc))
    except KeyError:
        return tool_error("unknown_window", "window_id 不存在")
    except Exception as exc:
        return tool_error("system_operation_failed", str(exc), retryable=False)


PUBLIC_TOOL_NAMES = {"computer_app", "computer_state", "computer_action", "computer_system"}
