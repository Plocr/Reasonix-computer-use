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
    return {
        "desktop_task": desktop_task,
        "process_required": process_required,
        "user_requested_cli": user_requested_cli,
        "computer_attempts": 0,
        "computer_failures": 0,
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


def handle(payload: dict[str, Any]) -> dict[str, Any] | None:
    event = str(payload.get("hook_event_name") or payload.get("event") or "")
    key = _session_key(payload)
    if event == "SessionStart":
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                "明确要求桌面应用或GUI过程的任务必须先使用computer-use；路由门禁会阻止Shell/Python抢跑。"}}
    if event == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or payload.get("user_prompt") or "")
        state = classify_prompt(prompt)
        _write_state(key, state)
        if state["process_required"] and not state["user_requested_cli"]:
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                    "本任务明确指定桌面应用/GUI过程。先调用computer_app，不得用Bash、Python、公式或CLI替代指定步骤；可在目标应用内批量键入以减少动作。"}}
        return None
    state = _read_state(key)
    tool = _tool_name(payload)
    if event == "PostToolUse" and tool in COMPUTER_TOOLS:
        state["computer_attempts"] = int(state.get("computer_attempts", 0)) + 1
        if _tool_failed(payload):
            state["computer_failures"] = int(state.get("computer_failures", 0)) + 1
        _write_state(key, state)
        return None
    if event == "PreToolUse" and tool in SHELL_TOOLS:
        if state.get("desktop_task") and state.get("process_required") and not state.get("user_requested_cli"):
            return {"hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "deny",
                "permissionDecisionReason":
                    "用户明确要求桌面应用或GUI过程；Shell/Python不能替代。请从computer_app开始或继续computer_state/computer_action。",
                "additionalContext":
                    "不要计算或生成最终文件来冒充GUI步骤。应用不可用时先搜索StartApps/同类应用，仍失败再向用户说明。",
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
