"""Reasonix hook gate for Computer Use plugin.

Only activates when computer-use tools are actually used.
Does NOT inject context on every prompt (saves tokens).
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

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
        return "current-task"
    import hashlib
    return hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()[:24]


def _read_state(key: str) -> dict[str, Any]:
    if not key:
        return {}
    try:
        import json
        value = json.loads((_state_root() / f"{key}.json").read_text(encoding="utf-8"))
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
    path = _state_root() / f"{key}.json"
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


def handle(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Minimal hook handler - only acts when computer-use tools are used."""
    event = str(payload.get("hook_event_name") or payload.get("event") or "")
    key = _session_key(payload)
    tool = _tool_name(payload)

    # Only react to computer-use tools
    if tool not in COMPUTER_TOOLS:
        return None

    if event == "PostToolUse":
        state = _read_state(key)
        state["computer_attempts"] = int(state.get("computer_attempts", 0)) + 1
        if _tool_failed(payload):
            state["computer_failures"] = int(state.get("computer_failures", 0)) + 1
        if _tool_blocked(payload):
            state["blocked_seen"] = True
        _write_state(key, state)

        if state.get("blocked_seen"):
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext":
                    "Computer Use 已返回 blocked=true。立即停止重复操作。"}}

    return None
