"""Fail-closed activation gate for the Reasonix Computer Use operator."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from reasonix_computer_use.vision_router import compact_route, resolve_vision_route


COMPUTER_TOOLS = {"computer_app", "computer_state", "computer_action", "computer_system",
                    "screen_interactor", "web_navigator"}
# Hidden tools (mouse, keyboard, screenshot, recorder) are NOT exposed to agents;
# they are used internally by screen_interactor.
OPERATOR_ALLOWED_TOOLS = COMPUTER_TOOLS | {"skill", "askuserquestion", "ask_user_question"}
ACTIVATION_MARKERS = ("/computer-use:run", "/computer-use:agent:operator")


def _plugin_root() -> Path:
    configured = os.environ.get("REASONIX_PLUGIN_ROOT", "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parent.parent


def _state_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "Reasonix" / "computer-use" / "hook-state"


def _session_key(payload: dict[str, Any]) -> str:
    value = (payload.get("session_id") or payload.get("sessionId")
             or payload.get("thread_id") or payload.get("threadId")
             or payload.get("conversation_id") or payload.get("conversationId")
             or payload.get("task_id") or payload.get("taskId")
             or payload.get("transcript_path") or payload.get("transcriptPath")
             or os.environ.get("REASONIX_SESSION_ID") or os.environ.get("REASONIX_THREAD_ID") or "")
    if not value:
        return ""
    return hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()[:24]


def _state_path(key: str) -> Path:
    return _state_root() / f"{key}.json"


def _read_state(key: str) -> dict[str, Any]:
    if not key:
        return {}
    try:
        value = json.loads(_state_path(key).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or time.time() - float(value.get("updated_at", 0)) > 1800:
            return {}
        return value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {}


def _write_state(key: str, state: dict[str, Any]) -> None:
    if not key:
        return
    state = {**state, "updated_at": time.time()}
    path = _state_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".route-guard.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _clear_state(key: str) -> None:
    if not key:
        return
    try:
        _state_path(key).unlink(missing_ok=True)
    except OSError:
        pass


def _finish_state_trace(state: dict[str, Any], status: str) -> None:
    trace_id = str(state.get("trace_id", ""))
    if not trace_id:
        return
    try:
        from reasonix_computer_use.trace import finish_trace
        finish_trace(trace_id, status, {
            "computer_attempts": state.get("computer_attempts", 0),
            "computer_failures": state.get("computer_failures", 0),
        })
    except Exception:
        pass


def _tool_name(payload: dict[str, Any]) -> str:
    raw = _raw_tool_name(payload)
    return raw.rsplit("__", 1)[-1].strip()


def _raw_tool_name(payload: dict[str, Any]) -> str:
    return str(payload.get("tool_name") or payload.get("toolName")
               or os.environ.get("REASONIX_TOOL_NAME") or "").casefold().strip()


def _external_vision_tool_allowed(payload: dict[str, Any]) -> bool:
    route = resolve_vision_route(payload, base_dir=_plugin_root())
    if route.mode != "external" or not route.server or not route.tool:
        return False
    expected = f"mcp__{route.server}__{route.tool}".casefold()
    return _raw_tool_name(payload) == expected


def _looks_like_broken_computer_tool(payload: dict[str, Any], tool: str) -> bool:
    raw = str(payload.get("tool_name") or payload.get("toolName") or "").casefold()
    return tool not in COMPUTER_TOOLS and ("computer-use" in raw or "computer_" in raw)


def _prompt(payload: dict[str, Any]) -> str:
    return str(payload.get("prompt") or payload.get("user_prompt") or payload.get("userPrompt") or "")


def _is_activation(prompt: str) -> tuple[bool, str]:
    lowered = prompt.casefold().strip()
    for marker in ACTIVATION_MARKERS:
        if lowered == marker or lowered.startswith(marker + " "):
            return True, marker.rsplit(":", 1)[-1]
    # Reasonix may pass the command name separately after command expansion.
    command = os.environ.get("REASONIX_COMMAND", "").casefold().strip()
    if command in ACTIVATION_MARKERS:
        return True, command.rsplit(":", 1)[-1]
    return False, ""


def _activation_state(prompt: str, source: str) -> dict[str, Any]:
    goal = prompt.split(" ", 1)[1].strip() if " " in prompt else ""
    return {
        "enabled": True,
        "activation_source": source,
        "goal_hash": hashlib.sha256(goal.encode("utf-8", "replace")).hexdigest()[:16],
        "computer_attempts": 0,
        "computer_failures": 0,
        "blocked_seen": False,
        "visual_seen": False,
    }


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_result", payload.get("toolResult", payload.get("result", {})))
    if isinstance(value, dict):
        # MCP hosts may wrap the JSON tool result in content[].text. Prefer a
        # structured top-level object, then inspect the first JSON text block.
        if any(key in value for key in ("status", "code", "blocked", "verified")):
            return value
        content = value.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                parsed = _result({"tool_result": block.get("text", "")})
                if parsed:
                    return parsed
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _tool_args(payload: dict[str, Any]) -> dict[str, Any]:
    """Read native or Claude-compatible tool arguments without guessing."""
    raw = payload.get("tool_args", payload.get("toolArgs", payload.get("tool_input", {})))
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _vision_context(payload: dict[str, Any]) -> str:
    route = compact_route(payload, base_dir=_plugin_root())
    if route.get("mode") == "native":
        return "Computer Use vision route: native image input is explicitly enabled."
    if route.get("mode") == "external":
        target = "/".join(item for item in (route.get("server", ""), route.get("tool", "")) if item)
        return ("Computer Use vision route: external handoff required (" +
                (target or "configured route") + "); do not claim to understand the image locally.")
    return "Computer Use vision route: vision_unavailable; do not infer screenshot contents or coordinates from an image placeholder."


def handle(payload: dict[str, Any]) -> dict[str, Any] | None:
    event = str(payload.get("hook_event_name") or payload.get("event") or "")
    key = _session_key(payload)
    tool = _tool_name(payload)

    if event == "SessionStart":
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                "Computer Use 默认禁用；仅在用户执行 /computer-use:run <任务> 后由 operator 使用。"}}

    if event == "UserPromptSubmit":
        prompt = _prompt(payload)
        activate, source = _is_activation(prompt)
        previous_state = _read_state(key)
        if not activate:
            _finish_state_trace(previous_state, "cancelled")
            _clear_state(key)
            return None
        if not key:
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                    "Computer Use 激活失败：activation_context_missing。当前 Reasonix 未提供可靠的 session/thread/transcript 标识。"}}
        _finish_state_trace(previous_state, "cancelled")
        state = _activation_state(prompt, source)
        try:
            from reasonix_computer_use.trace import start_trace
            state["trace_id"] = start_trace("operator", metadata={
                "activation_source": source, "goal_hash": state["goal_hash"]})
        except Exception:
            state["trace_id"] = ""
        _write_state(key, state)
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                "Computer Use 已为当前任务激活，当前命令已经展开。禁止调用 slash_command、再次调用 run/operator 命令或枚举命令列表；直接按 operator 契约从 screen_interactor 开始。任务结束后必须停止并清理激活状态。 "
                + _vision_context(payload)}}

    state = _read_state(key)
    if event == "PreToolUse" and state.get("enabled") and not tool:
        return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                "permissionDecisionReason": "tool_name_missing：无法确认当前调用属于 Computer Use 白名单。",
                "additionalContext": "停止当前调用；宿主必须提供稳定的 tool_name 后再继续。"}}
    if event == "PreToolUse" and tool in COMPUTER_TOOLS:
        if not key:
            return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                    "permissionDecisionReason": "activation_context_missing：无法安全隔离当前任务，Computer Use 已失败关闭。",
                    "additionalContext": "请在支持 session/thread/transcript 标识的 Reasonix 会话中使用 /computer-use:run <任务>。"}}
        if not state.get("enabled"):
            return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                    "permissionDecisionReason": "Computer Use 未显式激活。",
                    "additionalContext": "请让用户使用 /computer-use:run <任务>，不要在普通会话中调用桌面工具。"}}
        if state.get("blocked_seen"):
            return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                    "permissionDecisionReason": "当前任务已 blocked，禁止重新开始或重复调用。",
                    "additionalContext": "报告最小阻断并请求用户介入。"}}
        if tool == "computer_state" or tool == "screen_interactor":
            args = _tool_args(payload)
            if str(args.get("mode", "auto")).casefold() == "visual":
                route = resolve_vision_route(payload, base_dir=_plugin_root())
                if route.mode == "unavailable":
                    code = "vision_unavailable"
                    return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                            "permissionDecisionReason": code,
                            "additionalContext": _vision_context(payload) +
                            " 请切换到已声明图片能力的模型，或由宿主已配置的视觉路由处理；不要自行猜坐标。"}}

    if event == "PreToolUse" and _looks_like_broken_computer_tool(payload, tool):
        if key and state.get("enabled"):
            state["invalid_tool_seen"] = True
            _write_state(key, state)
        return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                "permissionDecisionReason": "Computer Use 工具名无效；禁止继续猜测或拼接 XML 残片。",
                "additionalContext": "规范示例：mcp__computer-use__screen_interactor。修正一次；再次失败就停止并报告。"}}

    if (event == "PreToolUse" and state.get("enabled") and tool
            and tool not in OPERATOR_ALLOWED_TOOLS and not _external_vision_tool_allowed(payload)):
        return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                "permissionDecisionReason": "当前显式 Computer Use 任务限制为四个领域工具、Skill 和 AskUserQuestion。",
                "additionalContext": "不得用 Shell、Python、文件工具、浏览器工具或其他 Agent 替代要求的 GUI 方法。"}}

    if event == "PostToolUse" and tool in COMPUTER_TOOLS and state.get("enabled"):
        result = _result(payload)
        vision_notice = result.get("code") in {"vision_unavailable", "vision_handoff_required"}
        state["computer_attempts"] = int(state.get("computer_attempts", 0)) + 1
        failed = result.get("status") == "error"
        if failed:
            state["computer_failures"] = int(state.get("computer_failures", 0)) + 1
        if result.get("blocked"):
            state["blocked_seen"] = True
        if tool in ("computer_state", "screen_interactor") and result.get("source") == "visual":
            state["visual_seen"] = True
        completion = result.get("task_completion", {})
        if not isinstance(completion, dict):
            completion = {}
        if not completion and isinstance(result.get("window"), dict):
            completion = result["window"].get("task_completion", {})
        if isinstance(completion, dict) and completion.get("status") != "unbound":
            state["task_completion_bound"] = True
            state["task_completion_verified"] = bool(completion.get("verified"))
            state["task_completion_pending"] = [str(item) for item in completion.get("pending", [])]
        if state.get("trace_id"):
            try:
                from reasonix_computer_use.trace import record_event
                args = _tool_args(payload)
                trace_event = {
                    "computer_app": "window_revision",
                    "computer_state": "perception",
                    "computer_action": "action",
                    "computer_system": "environment",
                    "screen_interactor": "interaction",
                    "web_navigator": "web",
                }.get(tool, "verification")
                trace_data: dict[str, Any] = {
                    "tool": tool,
                    "status": result.get("status"),
                    "code": result.get("code"),
                    "blocked": bool(result.get("blocked")),
                    "revision": result.get("revision") or (
                        result.get("window", {}).get("revision", "")
                        if isinstance(result.get("window", {}), dict) else ""),
                }
                if tool in ("computer_state", "screen_interactor"):
                    trace_data.update({"mode": args.get("mode", "auto"),
                                       "source": result.get("source"),
                                       "sufficient": result.get("sufficient"),
                                       "element_count": len(result.get("elements", []))})
                elif tool in ("computer_action", "screen_interactor"):
                    trace_data.update({"actions": args.get("actions", []),
                                       "verification": result.get("verification", {})})
                else:
                    trace_data["operation"] = args.get("operation", "")
                record_event(state["trace_id"], trace_event, trace_data)
                record_event(state["trace_id"], "verification", {
                    "tool": tool, "status": result.get("status"), "code": result.get("code"),
                    "blocked": bool(result.get("blocked")),
                    "revision": result.get("revision") or result.get("window", {}).get("revision", ""),
                })
            except Exception:
                pass
        _write_state(key, state)
        if state.get("blocked_seen"):
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                    "工具已返回 blocked:true。停止 Computer Use，不得用脚本或新流程冒充完成。"}}
        if vision_notice:
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                    "截图未被当前模型理解，任务不能依据视觉猜测继续。" + _vision_context(payload)}}
        if completion.get("status") == "pending":
            pending = ", ".join(str(item) for item in completion.get("pending", [])) or "required evidence"
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                    f"Computer Use 任务尚未完成，pending: {pending}。启动时已有的标题、状态栏文字、"
                    "搜索结果或静态截图都不是本任务产生的完成凭据；不得在最终回答中宣称完成。"}}

    if (event == "PostToolUse" and state.get("enabled")
            and _external_vision_tool_allowed(payload)):
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                "视觉结果只属于感知证据，不能直接完成 Computer Use 任务。即使截图显示目标标题、"
                "状态栏或暂停图标，也必须由后续 computer_action 返回 task_completion.verified=true；"
                "否则如实报告未完成。"}}

    if event == "Stop":
        completion_incomplete = (state.get("task_completion_bound")
                                 and not state.get("task_completion_verified"))
        finished = ("blocked" if state.get("blocked_seen") else
                    "incomplete" if completion_incomplete else "completed")
        _finish_state_trace(state, finished)
        _clear_state(key)
        if state.get("enabled") and completion_incomplete:
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                    "Computer Use completion gate: incomplete。最终回答必须说明仍缺少的语义凭据，"
                    "不得把窗口标题、状态栏、静态视觉或已发送点击描述成任务完成。"}}
    return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except (EOFError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    result = handle(payload)
    if result:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
