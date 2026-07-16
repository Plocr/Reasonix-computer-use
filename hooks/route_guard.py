"""Reasonix hook gate that keeps explicit desktop workflows on Computer Use."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


DESKTOP_MARKERS = (
    "桌面", "窗口", "点击", "打开", "启动", "退出", "浏览器", "单元格", "表格",
    "excel", "wps", "计算器", "calculator", "qq", "ollama", "记事本", "设置",
)
PROCESS_MARKERS = (
    "使用计算器", "使用计算器应用", "使用应用", "逐个相加", "点击", "单元格", "打开",
    "启动", "退出", "播放", "登录", "切换主题", "保存文件",
)
STRICT_GUI_MARKERS = (
    "必须使用", "必须用", "只能使用", "只能用", "不要脚本", "禁止脚本", "不要python",
    "不许用python", "逐个点击", "逐一点击", "一个个点击", "点击计算", "亲自点击",
)
USER_CLI_MARKERS = ("用python", "使用python", "python脚本", "用脚本", "命令行", "powershell", "bash", " cli")
SHELL_TOOLS = {"bash", "shell", "powershell", "terminal", "computer"}
COMPUTER_TOOLS = {"computer_app", "computer_state", "computer_action", "computer_system"}


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
        # Some Reasonix desktop builds do not include a session id in hook
        # payloads. UserPromptSubmit still runs before PreToolUse, so a short-
        # lived current-task key keeps routing effective across hook processes.
        return "current-task"
    return hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()[:24]


def _state_path(key: str) -> Path:
    return _state_root() / f"{key}.json"


def _read_state(key: str) -> dict[str, Any]:
    if not key:
        return {}
    try:
        value = json.loads(_state_path(key).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return {}
        if time.time() - float(value.get("updated_at", 0)) > 300:
            return {}
        return value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
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


def classify_prompt(prompt: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", "", prompt).casefold()
    marker_count = sum(marker.casefold() in compact for marker in DESKTOP_MARKERS)
    user_requested_cli = any(marker in compact for marker in USER_CLI_MARKERS)
    desktop_task = marker_count >= 2 or any(marker in compact for marker in ("单元格", "计算器应用", "桌面新建"))
    process_required = desktop_task and any(marker in compact for marker in PROCESS_MARKERS)
    strict_gui = process_required and any(marker in compact for marker in STRICT_GUI_MARKERS)
    mode = "result_only" if user_requested_cli or not desktop_task else ("strict_gui" if strict_gui else "gui_preferred")
    return {
        "mode": mode,
        "desktop_task": desktop_task,
        "process_required": process_required,
        "user_requested_cli": user_requested_cli,
        "computer_attempts": 0,
        "computer_failures": 0,
        "blocked_seen": False,
        "fallback_authorized": mode == "result_only",
    }


def _tool_name(payload: dict[str, Any]) -> str:
    raw = str(payload.get("tool_name") or payload.get("toolName")
              or os.environ.get("REASONIX_TOOL_NAME") or "").casefold()
    return raw.rsplit("__", 1)[-1]


def _tool_failed(payload: dict[str, Any]) -> bool:
    result = payload.get("tool_result", payload.get("toolResult", {}))
    if isinstance(result, str):
        lowered = result.casefold()
        return any(value in lowered for value in ('"status":"error"', '"blocked":true', "app_not_found"))
    if isinstance(result, dict):
        return result.get("status") == "error" or bool(result.get("blocked"))
    return False


def _tool_blocked(payload: dict[str, Any]) -> bool:
    result = payload.get("tool_result", payload.get("toolResult", {}))
    if isinstance(result, str):
        return '"blocked":true' in result.casefold()
    return bool(result.get("blocked")) if isinstance(result, dict) else False


def _fallback_ready(state: dict[str, Any]) -> bool:
    if state.get("mode") == "result_only":
        return True
    if state.get("mode") == "strict_gui":
        return False
    return bool(state.get("blocked_seen")) or (
        int(state.get("computer_attempts", 0)) >= 3
        and int(state.get("computer_failures", 0)) >= 2
    )


def handle(payload: dict[str, Any]) -> dict[str, Any] | None:
    event = str(payload.get("hook_event_name") or payload.get("event") or "")
    key = _session_key(payload)
    if event == "SessionStart":
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                "明确要求桌面应用或GUI过程的任务必须先使用computer-use；路由门禁会阻止Shell/Python抢跑。"}}
    if event == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or payload.get("user_prompt") or "")
        state = classify_prompt(prompt)
        try:
            from reasonix_computer_use.trace import start_trace
            state["trace_id"] = start_trace("hook-policy", metadata={"mode": state["mode"]})
        except Exception:
            state["trace_id"] = ""
        _write_state(key, state)
        if state["mode"] == "strict_gui":
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                    "本任务明确约束GUI执行过程，必须先使用computer-use；失败后请求用户介入，不得用Bash、Python、公式或CLI替代指定步骤。"}}
        if state["mode"] == "gui_preferred":
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                    "本任务优先使用computer-use；任务成功后立即停止。仅在尚未完成且累计至少3次Computer Use调用并有2次真实失败，或工具返回blocked=true后，才允许安全降级；降级时必须如实说明实际方法。"}}
        return None
    state = _read_state(key)
    tool = _tool_name(payload)
    if event == "PostToolUse" and tool in COMPUTER_TOOLS:
        state["computer_attempts"] = int(state.get("computer_attempts", 0)) + 1
        if _tool_failed(payload):
            state["computer_failures"] = int(state.get("computer_failures", 0)) + 1
        if _tool_blocked(payload):
            state["blocked_seen"] = True
        state["fallback_authorized"] = _fallback_ready(state)
        if state.get("trace_id"):
            try:
                from reasonix_computer_use.trace import record_event
                record_event(state["trace_id"], "strategy_transition", {
                    "mode": state.get("mode"), "computer_attempts": state.get("computer_attempts"),
                    "computer_failures": state.get("computer_failures"),
                    "blocked": state.get("blocked_seen"),
                    "fallback_authorized": state.get("fallback_authorized")})
            except Exception:
                pass
        _write_state(key, state)
        return None
    if event == "PreToolUse" and tool in SHELL_TOOLS:
        if state.get("mode") == "strict_gui":
            return {"hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "deny",
                "permissionDecisionReason":
                    "用户明确约束GUI执行过程；Shell/Python不能替代。请继续computer-use或请求用户介入。",
                "additionalContext":
                    "不要计算或生成最终文件来冒充GUI步骤。应用不可用时先搜索StartApps/同类应用，仍失败再向用户说明。",
            }}
        if state.get("mode") == "gui_preferred" and not _fallback_ready(state):
            return {"hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "deny",
                "permissionDecisionReason": "本任务应优先使用computer-use；尚未达到安全降级阈值。",
                "additionalContext": "至少3次Computer Use调用且2次真实失败，或blocked=true后才允许降级。",
            }}
        if state.get("mode") == "gui_preferred" and _fallback_ready(state):
            if state.get("trace_id"):
                try:
                    from reasonix_computer_use.trace import record_event
                    record_event(state["trace_id"], "fallback", {"authorized": True, "mode": "gui_preferred"})
                except Exception:
                    pass
            return {"hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": "已达到安全降级阈值。只允许等价且可逆的CLI/API方案；必须说明GUI失败点和实际方法，不得声称完成未执行的GUI步骤；外部写入和不可逆操作仍需确认。",
            }}
    return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    try:
        raw = sys.stdin.buffer.read()
        decoded = ""
        for encoding in ("utf-8-sig", "utf-16", "gb18030"):
            try:
                decoded = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        payload = json.loads(decoded) if decoded.strip() else {}
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
