"""Reasonix-facing domain tools for Windows computer use."""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .keyboard import VK_MAP, _send_key, computer_keyboard_press, computer_keyboard_type
from .mcp_server import register_tool
from .mouse import computer_mouse_click, computer_mouse_move, computer_mouse_scroll
from .runtime import (REGISTRY, STRATEGIES, WindowContext, memory_candidates, remember_success,
                      semantic_state, window_payload)
from .screenshot import _capture_window, _get_screenshot_dir
from .system_index import build_index, enrich_index, ensure_index, find_app, query_profile, search_apps
from .text_vision import find_text, scan_text
from .ui_tree import computer_act as uia_act
from .ui_tree import observe
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


def _public_app(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in
            ("id", "name", "version", "publisher", "path", "launch_target", "launch_args", "source", "confidence", "sha256")
            if item.get(key) not in (None, "")}


def _find_app_window(app: dict[str, Any], pid: int = 0, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    target = str(app.get("path", "")).casefold()
    name = str(app.get("name", "")).casefold()
    stem = Path(target).stem.casefold() if target else ""
    best = None
    while time.monotonic() < deadline:
        candidates = list_windows()
        exact = [item for item in candidates if target and item.process_path.casefold() == target]
        exact_title = [item for item in candidates if name and item.title.casefold() == name]
        process = [item for item in candidates if pid and not target.startswith("shell:") and item.pid == pid]
        titled = [item for item in candidates if name and item.title.casefold().startswith(name)
                  and item.class_name != "CabinetWClass"]
        executable = [item for item in candidates if stem and Path(item.process_path).stem.casefold() == stem]
        ranked = exact or exact_title or process or titled or executable
        if ranked:
            best = max(ranked, key=lambda item: (item.rect[2] - item.rect[0]) * (item.rect[3] - item.rect[1]))
            break
        time.sleep(0.15)
    return best


def _launch(app: dict[str, Any]) -> tuple[int, Any]:
    target = str(app.get("launch_target") or app.get("path") or "")
    if target.casefold().startswith("shell:appsfolder\\"):
        process = subprocess.Popen(["explorer.exe", target],
                                   creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        return process.pid, process
    if not target or not os.path.isfile(target):
        raise FileNotFoundError(f"应用启动目标不存在：{target or app.get('name')}")
    args = str(app.get("launch_args") or "")
    command = [target]
    if args:
        command.extend(shlex.split(args, posix=False))
    process = subprocess.Popen(command, cwd=str(Path(target).parent),
                               creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    return process.pid, process


def _prime_window_state(context: WindowContext, info) -> None:
    """Warm the target UIA provider during launch without returning its tree."""
    try:
        result = observe(hex(info.hwnd), "interactive", MAX_ELEMENTS)
        elements = result.get("elements", [])
        context.update(semantic_state(info, elements), "uia", elements)
    except Exception:
        context.update({"title": info.title, "rect": info.rect, "hwnd": info.hwnd}, "window")


@register_tool(
    name="computer_app",
    description="查找、启动、聚焦或关闭 Windows 应用。启动默认直接传 query；app_id 只能使用搜索结果。关闭优先复用启动响应的 window_id。禁止 Shell 搜索。",
    schema={"type": "object", "properties": {
        "operation": {"type": "string", "enum": ["search", "launch", "focus", "list_running", "close"]},
        "query": {"type": "string", "description": "应用名称。launch 时优先直接使用，例如 Notepad。"},
        "app_id": {"type": "string", "description": "仅使用 search 返回的 id；普通应用名应传 query。"},
        "window_id": {"type": "string", "description": "launch 返回的窗口 id；focus/close 应原样复用。"},
        "close_mode": {"type": "string", "enum": ["window", "process"], "default": "window"},
        "confirmed": {"type": "boolean", "default": False},
        "limit": {"type": "integer", "default": 10}}, "required": ["operation"]})
async def computer_app(args: dict) -> str:
    operation = args.get("operation")
    try:
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
        if operation == "launch":
            app = find_app(str(args.get("app_id", ""))) if args.get("app_id") else None
            normalized_query = ""
            if app is None and args.get("app_id") and not args.get("query"):
                # Be forgiving when a model puts an application name in app_id.
                normalized_query = str(args["app_id"])
                matches = search_apps(normalized_query, 10)
                app = matches[0] if matches else None
            if app is None and args.get("query"):
                matches = search_apps(str(args["query"]), 10)
                if not matches:
                    return tool_error("app_not_found", f"系统索引中没有找到应用：{args['query']}",
                                      fallback="请用户确认应用名称或安装位置")
                launchable = [item for item in matches if str(item.get("launch_target") or item.get("path") or "")]
                exact = [item for item in launchable if str(item.get("name", "")).casefold() == str(args["query"]).casefold()]
                app = (exact or launchable or matches)[0]
            if app is None:
                return tool_error("app_not_found", "必须提供有效 app_id 或 query")
            existing = _find_app_window(app, timeout=0.2)
            if existing:
                context = REGISTRY.register(existing, app)
                try:
                    activate_window(existing.hwnd)
                except OSError:
                    pass
                _prime_window_state(context, existing)
                return _ok(app=_public_app(app), window=window_payload(context, existing), reused=True,
                           next_hint="调用 computer_state 获取当前窗口状态")
            try:
                pid, _ = _launch(app)
            except FileNotFoundError:
                build_index(f"stale-launch-target:{app.get('name')}")
                refreshed = find_app(str(app.get("id", "")))
                if not refreshed:
                    return tool_error("stale_app_path", "应用路径已失效，索引已刷新", retryable=True)
                app = refreshed
                pid, _ = _launch(app)
            info = _find_app_window(app, pid)
            if not info:
                return tool_error("window_not_found", "应用已启动，但等待 8 秒后仍未发现窗口", retryable=True,
                                  fallback="调用 computer_app(list_running) 查看新窗口")
            context = REGISTRY.register(info, app)
            _prime_window_state(context, info)
            payload = {"app": _public_app(app), "window": window_payload(context, info),
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
    except KeyError:
        return tool_error("unknown_window", "window_id 不存在；请调用 computer_app(list_running)")
    except Exception as exc:
        return tool_error("app_operation_failed", str(exc), retryable=True)


def _goal_terms(goal: str) -> list[str]:
    value = re.sub(r"[，。,.!?;；:：()（）]", " ", goal).strip().casefold()
    terms = [part for part in value.split() if len(part) > 1]
    for prefix in ("打开", "点击", "进入", "查看", "选择", "切换", "关闭", "找到"):
        if value.startswith(prefix) and len(value) > len(prefix):
            terms.append(value[len(prefix):])
    return list(dict.fromkeys(terms))[:8]


def _relevant(items: list[dict[str, Any]], goal: str, limit: int) -> list[dict[str, Any]]:
    matches = _goal_matches(items, goal)
    return [_bounded_element(item) for item in (matches or items)[:limit]]


def _goal_matches(items: list[dict[str, Any]], goal: str) -> list[dict[str, Any]]:
    terms = _goal_terms(goal)
    if not terms:
        return list(items)
    matches = []
    for item in items:
        text = " ".join(str(item.get(key, "")) for key in ("name", "value", "text")).casefold()
        if any(term in text or text in term for term in terms if text):
            matches.append(item)
    return matches


def _bounded_element(item: dict[str, Any]) -> dict[str, Any]:
    allowed = ("ref", "role", "name", "value", "rect", "actions", "id", "class", "confidence", "action")
    result = {key: item.get(key) for key in allowed if item.get(key) not in (None, "", [])}
    for key, maximum in (("name", 120), ("value", 120), ("id", 80), ("class", 80)):
        if key in result:
            value = str(result[key])
            result[key] = value if len(value) <= maximum else value[:maximum - 3] + "..."
    return result


def _ocr_elements(window_id: str, goal: str, limit: int) -> list[dict[str, Any]]:
    scanned = scan_text(window_id, 200)
    items = []
    for match in scanned.get("matches", []):
        if float(match.get("confidence", 0)) < OCR_MIN_CONFIDENCE:
            continue
        items.append({"ref": f"o{len(items) + 1}", "role": "text", "name": str(match.get("text", ""))[:120],
                      "rect": match.get("rect"), "confidence": match.get("confidence"), "action": "click"})
    return _relevant(items, goal, limit)


def _image_digest(image) -> str:
    small = image.convert("L").resize((32, 32))
    return hashlib.sha1(small.tobytes()).hexdigest()


@register_tool(
    name="computer_state",
    description="观察一个 Windows 窗口。固定按应用记忆、UIA、本地 OCR、窗口视觉的顺序回退，并只返回与目标相关的紧凑状态。",
    schema={"type": "object", "properties": {
        "window_id": {"type": "string"}, "goal": {"type": "string"},
        "mode": {"type": "string", "enum": ["auto", "uia", "ocr", "visual"], "default": "auto"},
        "since_revision": {"type": "string"}, "limit": {"type": "integer", "default": 40}},
        "required": ["window_id", "goal"]})
async def computer_state(args: dict) -> str:
    try:
        context = REGISTRY.get(str(args["window_id"]))
        info = context.info()
        goal = str(args.get("goal", ""))
        limit = max(1, min(int(args.get("limit", DEFAULT_ELEMENTS)), MAX_ELEMENTS))
        mode = args.get("mode", "auto")
        errors: dict[str, str] = {}
        memory = memory_candidates(context, goal)
        elements: list[dict[str, Any]] = []
        same_revision = args.get("since_revision") == context.revision
        cached_matches = _goal_matches(context.elements, goal) if same_revision else []
        if same_revision and context.source == "uia" and cached_matches and context.strategy_level < 2:
            return _ok(window=window_payload(context, info), revision=context.revision, source="uia",
                       unchanged=True, elements=[_bounded_element(item) for item in cached_matches[:limit]],
                       progress="复用当前 revision 的 UIA 缓存", blocked=False,
                       next_hint="不要重复观察；使用缓存 ref 执行动作")
        if same_revision and context.source == "ocr" and cached_matches and context.strategy_level < 3:
            return _ok(window=window_payload(context, info), revision=context.revision, source="ocr",
                       unchanged=True, elements=[_bounded_element(item) for item in cached_matches[:limit]],
                       progress="复用当前 revision 的 OCR 缓存", blocked=False,
                       next_hint="使用 click_text，不调用外部视觉")
        try:
            if same_revision and context.source in ("uia", "ocr"):
                raise RuntimeError("cached_revision_has_no_matching_target")
            result = observe(hex(info.hwnd), "interactive", MAX_ELEMENTS)
            all_elements = result.get("elements", [])
            elements = _relevant(all_elements, goal, limit)
            state = semantic_state(info, all_elements)
            context.update(state, "uia", all_elements)
            if memory:
                labels = {str(item.get("name", "")).casefold() for item in all_elements}
                memory = [item for item in memory if str(item.get("action", {}).get("text", "")).casefold() in labels]
            if memory:
                return _ok(window=window_payload(context, info), revision=context.revision, source="memory",
                           elements=elements, memory_hits=len(memory), progress="已命中该应用的验证成功路径",
                           blocked=False, next_hint="优先使用返回的 ref 调用 computer_action")
            relevant_names = [str(item.get("name", "")) for item in elements]
            if context.strategy_level < 2 and elements and (mode == "uia" or any(term in " ".join(relevant_names).casefold() for term in _goal_terms(goal))):
                return _ok(window=window_payload(context, info), revision=context.revision, source="uia",
                           elements=elements, progress="UIA 已找到相关控件", blocked=False,
                           next_hint="使用 click_ref；无需截图")
        except Exception as exc:
            if str(exc) != "cached_revision_has_no_matching_target":
                errors["uia"] = str(exc)[:300]
        if mode != "uia":
            try:
                ocr_items = _ocr_elements(hex(info.hwnd), goal, limit)
                if ocr_items and context.strategy_level < 3:
                    ocr_state = {"window": info.title, "texts": [(item["name"], item["rect"]) for item in ocr_items]}
                    context.update(ocr_state, "ocr", ocr_items)
                    return _ok(window=window_payload(context, info), revision=context.revision, source="ocr",
                               elements=ocr_items, progress="本地 OCR 已找到相关文字", blocked=False,
                               next_hint="使用 click_text；不会调用外部视觉模型")
            except Exception as exc:
                errors["ocr"] = str(exc)[:300]
        if mode in ("auto", "visual"):
            if context.visual_sent_for_revision == context.revision:
                context.fail()
                return _ok(window=window_payload(context, info), revision=context.revision, source="none",
                           unchanged=True, progress="当前 revision 已经返回过视觉图片", blocked=True,
                           next_hint="不要重复请求图片；执行新策略或请用户介入", errors=errors)
            image, current = _capture_window(hex(info.hwnd), activate=False)
            digest = _image_digest(image)
            if digest == context.image_hash:
                context.fail()
                return _ok(window=window_payload(context, current), revision=context.revision, source="none",
                           unchanged=True, progress="当前窗口与上一张视觉图片相同", blocked=True,
                           next_hint="不要重复截图或点击；请用户说明当前界面或接管操作", errors=errors)
            context.image_hash = digest
            context.visual_sent_for_revision = context.revision
            context.source = "visual"
            context.elements = []
            path = Path(_get_screenshot_dir()) / f"state_{context.window_id}_{context.revision}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(path, "PNG")
            left, top, right, bottom = current.rect
            return _ok(window=window_payload(context, current), revision=context.revision, source="visual",
                       image_path=str(path.resolve()), origin=[left, top], size=[right - left, bottom - top],
                       progress="UIA 与 OCR 均不足，返回当前窗口唯一视觉图片", blocked=False,
                       next_hint="直接理解本工具附带图片，并在当前 revision 上调用 click_point", errors=errors)
        context.fail()
        return _ok(window=window_payload(context, info), revision=context.revision, source="none", elements=[],
                   progress="当前感知模式未找到目标", blocked=True,
                   next_hint=f"升级到 {STRATEGIES[context.strategy_level]} 或请求用户介入", errors=errors)
    except KeyError:
        return tool_error("unknown_window", "window_id 不存在；请先调用 computer_app")
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
    if action.get("relative", False):
        x, y = left + x, top + y
    if not (left <= x < right and top <= y < bottom):
        raise ValueError("坐标不在当前窗口物理像素范围内")
    return x, y


def _action_sensitive(context: WindowContext, action: dict[str, Any]) -> bool:
    if action.get("confirmed"):
        return False
    keys = ("name", "purpose") if action.get("type") == "type" else ("text", "name", "purpose")
    target = " ".join(str(action.get(key, "")) for key in keys).casefold()
    if any(word in target for word in SENSITIVE_WORDS):
        return True
    if action.get("type") == "click_ref":
        element = next((item for item in context.elements if item.get("ref") == action.get("ref")), {})
        label = str(element.get("name", "")).casefold()
        return any(word in label for word in SENSITIVE_WORDS)
    return bool(action.get("sensitive"))


async def _click_ref(context: WindowContext, ref: str) -> dict[str, Any]:
    element = next((item for item in context.elements if item.get("ref") == ref), None)
    if not element:
        return {"status": "error", "code": "stale_ref", "message": "ref 不属于当前 revision"}
    actions = element.get("actions", [])
    semantic = "invoke"
    for candidate in ("invoke", "toggle", "select", "expand", "focus", "click"):
        if candidate in actions:
            semantic = candidate
            break
    return _parse_result(await uia_act({"ref": ref, "action": semantic, "verify": True}))


async def _execute(context: WindowContext, action: dict[str, Any]) -> dict[str, Any]:
    kind = action.get("type")
    if kind == "click_ref":
        return await _click_ref(context, str(action.get("ref", "")))
    if kind == "click_text":
        text = str(action.get("text", ""))
        matches = [item for item in context.elements if text.casefold() in str(item.get("name", "")).casefold()]
        if matches and str(matches[0].get("ref", "")).startswith("e"):
            return await _click_ref(context, str(matches[0]["ref"]))
        result = find_text(hex(context.hwnd), text, bool(action.get("exact", False)), 20)
        candidates = [item for item in result.get("matches", []) if float(item.get("confidence", 0)) >= OCR_MIN_CONFIDENCE]
        index = int(action.get("index", 0))
        if index < 0 or index >= len(candidates):
            return {"status": "error", "code": "text_not_found", "message": f"OCR 未可靠找到文字：{text}"}
        left, top, right, bottom = candidates[index]["rect"]
        x, y = (left + right) // 2, (top + bottom) // 2
        return _parse_result(await computer_mouse_click({"x": x, "y": y, "button": "left"}))
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
    if kind == "type":
        return _parse_result(await computer_keyboard_type({"text": str(action.get("text", "")),
                                                            "interval": min(float(action.get("interval", 0.01)), 0.1)}))
    if kind == "press":
        keys = action.get("keys", [])
        keys = [keys] if isinstance(keys, str) else list(keys)
        if not keys:
            return {"status": "error", "code": "missing_keys"}
        return _parse_result(await computer_keyboard_press({"key": keys[-1], "modifiers": keys[:-1]}))
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


def _adopt_new_window(context: WindowContext, old_handles: set[int]) -> None:
    foreground = user32.GetForegroundWindow()
    if foreground and foreground not in old_handles:
        try:
            info = get_window_info(foreground)
            belongs = (context.app_path and info.process_path.casefold() == context.app_path.casefold())
            belongs = belongs or (context.app_name and context.app_name.casefold() in info.title.casefold())
            if belongs:
                context.hwnd = foreground
                return
        except (ValueError, OSError):
            pass
    if context.app_path:
        candidates = [item for item in list_windows() if item.process_path.casefold() == context.app_path.casefold()]
        if candidates:
            best = max(candidates, key=lambda item: (item.rect[2] - item.rect[0]) * (item.rect[3] - item.rect[1]))
            context.hwnd = best.hwnd


def _refresh_semantic(context: WindowContext) -> bool:
    info = context.info()
    try:
        result = observe(hex(info.hwnd), "interactive", MAX_ELEMENTS)
        elements = result.get("elements", [])
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
            items = observe(hex(info.hwnd), "interactive", MAX_ELEMENTS).get("elements", [])
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


def _verify(context: WindowContext, expect: dict[str, Any], changed: bool) -> dict[str, Any]:
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
    return {"verified": True, "method": "semantic-change" if changed else "action-result", "changed": changed}


@register_tool(
    name="computer_action",
    description="在最新窗口 revision 上批量执行最多五个动作。优先 UIA，其次 OCR，坐标仅用于视觉保底；每步失败立即停止。",
    schema={"type": "object", "properties": {
        "window_id": {"type": "string"}, "revision": {"type": "string"},
        "actions": {"type": "array", "minItems": 1, "maxItems": 5, "items": {"type": "object"}},
        "expect": {"type": "object"}}, "required": ["window_id", "revision", "actions"]})
async def computer_action(args: dict) -> str:
    try:
        context = REGISTRY.get(str(args["window_id"]))
        if str(args.get("revision")) != context.revision:
            return tool_error("stale_revision", "窗口状态已变化；请调用 computer_state 获取最新 revision")
        actions = args.get("actions")
        if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_BATCH:
            return tool_error("invalid_batch", "actions 必须包含 1 到 5 个动作")
        for action in actions:
            if _action_sensitive(context, action):
                return tool_error("confirmation_required", "检测到密码、验证码、支付、删除或协议操作，需要用户确认或接管")
        results = []
        before = context.state_hash
        memory_actions = []
        needs_pixel_verification = any(action.get("type") in
                                       ("click_point", "double_click", "right_click", "middle_click", "drag")
                                       for action in actions)
        before_pixel = ""
        if needs_pixel_verification:
            try:
                before_pixel = _image_digest(_capture_window(hex(context.hwnd), activate=False)[0])
            except Exception:
                pass
        old_handles = {item.hwnd for item in list_windows()}
        for index, action in enumerate(actions):
            memory_action = dict(action)
            if action.get("type") == "click_ref":
                element = next((item for item in context.elements if item.get("ref") == action.get("ref")), None)
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
            result = await _execute(context, action)
            safe_result = {key: value for key, value in result.items() if key not in ("text", "value", "clipboard")}
            results.append({"index": index, **safe_result})
            if result.get("status") != "ok":
                level = context.fail()
                return parse_result({"status": "error", "code": "batch_stopped", "results": results,
                                     "progress": "动作失败，后续动作未执行", "blocked": level >= 3,
                                     "next_hint": f"重新观察并升级到 {STRATEGIES[level]}"})
            _adopt_new_window(context, old_handles)
            await _wait_stable(context)
        changed = _refresh_semantic(context)
        if needs_pixel_verification and before_pixel:
            try:
                after_pixel = _image_digest(_capture_window(hex(context.hwnd), activate=False)[0])
                changed = changed or after_pixel != before_pixel
            except Exception:
                pass
        verification = _verify(context, args.get("expect", {}), changed)
        if not verification.get("verified"):
            level = context.fail()
            return parse_result({"status": "error", "code": "verification_failed", "results": results,
                                 "verification": verification, "revision": context.revision,
                                 "progress": "动作已执行但预期状态未出现", "blocked": level >= 3,
                                 "next_hint": f"调用 computer_state，监督器将升级到 {STRATEGIES[level]}"})
        for action in memory_actions:
            remember_success(context, action, before, context.state_hash)
        return _ok(window=window_payload(context), revision=context.revision, results=results,
                   verification=verification, progress="动作已完成并通过本地验证", blocked=False,
                   next_hint="目标已完成则停止；否则用最新 revision 继续 computer_state/computer_action")
    except KeyError:
        return tool_error("unknown_window", "window_id 不存在；请先调用 computer_app")
    except Exception as exc:
        return tool_error("action_failed", str(exc), retryable=True)


READ_ONLY_COMMANDS = ("where ", "where.exe ", "get-", "dir ", "ls ", "type ", "whoami", "systeminfo", "tasklist")
DANGEROUS_COMMANDS = ("remove-item", " del ", "erase ", "format ", "shutdown", "restart-computer", "stop-process",
                      "reg add", "reg delete", "invoke-expression", "iex ", "rm ")


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
    description="查询或刷新系统画像，执行诊断、受控文件操作、窗口管理或受限命令。写入和不可逆操作必须显式确认。",
    schema={"type": "object", "properties": {
        "operation": {"type": "string", "enum": ["profile", "refresh", "diagnose", "file", "window", "command"]},
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
            return _ok(version="0.8.0-alpha.0", platform_supported=os.name == "nt",
                       dpi_awareness=DPI_AWARENESS, virtual_screen=virtual_screen(),
                       displays=index.get("displays", []),
                       uia_available=importlib.util.find_spec("comtypes") is not None,
                       ocr_available=importlib.util.find_spec("rapidocr_onnxruntime") is not None,
                       input_available=bool(user32), public_tools=4)
        if operation == "file":
            return _ok(result=_file_operation(target, params))
        if operation == "window":
            return _ok(window=_window_operation(target, params))
        if operation == "command":
            command = target.strip()
            lowered = f" {command.casefold()} "
            if any(word in lowered for word in DANGEROUS_COMMANDS):
                return tool_error("command_blocked", "命令包含删除、终止进程或系统修改")
            read_only = command.casefold().startswith(READ_ONLY_COMMANDS)
            if not read_only and not params.get("confirmed"):
                return tool_error("confirmation_required", "非只读命令需要 confirmed=true")
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
