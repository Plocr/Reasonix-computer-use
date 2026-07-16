"""Window state, adaptive fallback and verified application memory."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .system_profile import memory_dir
from .windows import WindowInfo, get_window_info, list_windows, user32, window_dpi


STRATEGIES = ("memory", "uia", "ocr", "visual")


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:12]


@dataclass
class WindowContext:
    window_id: str
    hwnd: int
    app_id: str = ""
    app_name: str = ""
    app_path: str = ""
    app_fingerprint: str = ""
    owner_pid: int = 0
    trace_id: str = ""
    revision_no: int = 0
    revision: str = "r0"
    state_hash: str = ""
    source_hashes: dict[str, str] = field(default_factory=dict)
    source: str = "none"
    elements: list[dict[str, Any]] = field(default_factory=list)
    image_hash: str = ""
    visual_sent_for_revision: str = ""
    failure_hash: str = ""
    failure_count: int = 0
    strategy_level: int = 1
    action_signatures: set[str] = field(default_factory=set)
    invalid_action_count: int = 0
    no_progress_count: int = 0
    hard_blocked: bool = False
    state_reads_without_action: int = 0
    focused_ref: str = ""

    def info(self) -> WindowInfo:
        if user32.IsWindow(self.hwnd):
            return get_window_info(self.hwnd)
        candidates = list_windows()
        if self.app_path:
            exact = [item for item in candidates if item.process_path.casefold() == self.app_path.casefold()]
            if exact:
                self.hwnd = exact[0].hwnd
                return exact[0]
        if self.owner_pid:
            owned = [item for item in candidates if item.pid == self.owner_pid]
            if owned:
                self.hwnd = owned[0].hwnd
                return owned[0]
        if self.app_name:
            named = [item for item in candidates if self.app_name.casefold() == item.title.casefold()]
            if named:
                self.hwnd = named[0].hwnd
                return named[0]
        raise ValueError("目标窗口已经关闭，且没有找到该应用的新窗口")

    def update(self, state: Any, source: str, elements: list[dict[str, Any]] | None = None) -> bool:
        digest = _hash(state)
        previous = self.source_hashes.get(source)
        changed = (not self.state_hash) or (previous is not None and digest != previous)
        self.source_hashes[source] = digest
        if changed:
            self.revision_no += 1
            self.revision = f"r{self.revision_no}-{digest[:6]}"
            self.state_hash = digest
            self.failure_count = 0
            self.failure_hash = ""
            self.strategy_level = 1
            self.action_signatures.clear()
            self.invalid_action_count = 0
            self.no_progress_count = 0
            self.hard_blocked = False
            self.state_reads_without_action = 0
            self.focused_ref = ""
        self.source = source
        if elements is not None:
            self.elements = elements
        return changed

    def fail(self) -> int:
        self.no_progress_count += 1
        if self.failure_hash == self.state_hash:
            self.failure_count += 1
        else:
            self.failure_hash = self.state_hash
            self.failure_count = 1
        if self.failure_count >= 2:
            self.strategy_level = min(self.strategy_level + 1, len(STRATEGIES) - 1)
            self.failure_count = 0
        if self.no_progress_count >= 4:
            self.hard_blocked = True
        return self.strategy_level

    def invalid_action(self) -> bool:
        self.invalid_action_count += 1
        self.no_progress_count += 1
        if self.invalid_action_count >= 2 or self.no_progress_count >= 4:
            self.hard_blocked = True
        return self.hard_blocked

    def succeed(self) -> None:
        self.invalid_action_count = 0
        self.no_progress_count = 0
        self.hard_blocked = False
        self.state_reads_without_action = 0

    def state_read(self) -> bool:
        self.state_reads_without_action += 1
        if self.state_reads_without_action > 2:
            self.hard_blocked = True
        return self.hard_blocked


class WindowRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._next = 1
        self._contexts: dict[str, WindowContext] = {}

    def register(self, info: WindowInfo, app: dict[str, Any] | None = None) -> WindowContext:
        with self._lock:
            for context in self._contexts.values():
                if context.hwnd == info.hwnd:
                    context.owner_pid = info.pid
                    if app:
                        context.app_id = str(app.get("id", ""))
                        context.app_name = str(app.get("name", ""))
                        context.app_path = info.process_path or str(app.get("path", ""))
                        context.app_fingerprint = _hash({key: app.get(key, "") for key in ("id", "version", "sha256")})
                    return context
                if app and context.app_id and context.app_id == app.get("id"):
                    context.hwnd = info.hwnd
                    context.owner_pid = info.pid
                    context.app_path = info.process_path or str(app.get("path", ""))
                    context.app_fingerprint = _hash({key: app.get(key, "") for key in ("id", "version", "sha256")})
                    return context
            window_id = f"w{self._next}"
            self._next += 1
            context = WindowContext(
                window_id=window_id, hwnd=info.hwnd, app_id=str((app or {}).get("id", "")),
                app_name=str((app or {}).get("name", "")), app_path=info.process_path or str((app or {}).get("path", "")),
                app_fingerprint=_hash({key: (app or {}).get(key, "") for key in ("id", "version", "sha256")}),
                owner_pid=info.pid,
            )
            context.update(_window_state(info), "window")
            self._contexts[window_id] = context
            return context

    def get(self, window_id: str) -> WindowContext:
        with self._lock:
            if window_id in self._contexts:
                return self._contexts[window_id]
        if str(window_id).lower().startswith("0x") or str(window_id).isdigit():
            return self.register(get_window_info(int(str(window_id), 0)))
        raise KeyError(window_id)

    def find(self, app_id: str = "", query: str = "") -> WindowContext:
        with self._lock:
            contexts = list(self._contexts.values())
        if app_id:
            match = next((item for item in contexts if item.app_id == app_id), None)
            if match:
                return match
        if query:
            needle = query.casefold()
            match = next((item for item in contexts if needle in item.app_name.casefold()
                          or needle in item.info().title.casefold()), None)
            if match:
                return match
        raise KeyError(app_id or query)

    def running(self, limit: int = 20) -> list[dict[str, Any]]:
        result = []
        for info in list_windows()[:limit]:
            context = self.register(info)
            result.append(window_payload(context, info))
        return result


REGISTRY = WindowRegistry()


def _window_state(info: WindowInfo) -> dict[str, Any]:
    return {"hwnd": info.hwnd, "title": info.title, "class": info.class_name, "rect": info.rect,
            "pid": info.pid, "path": info.process_path}


def window_payload(context: WindowContext, info: WindowInfo | None = None) -> dict[str, Any]:
    info = info or context.info()
    left, top, right, bottom = info.rect
    return {
        "id": context.window_id, "title": info.title, "class": info.class_name,
        "pid": info.pid, "rect": [left, top, right, bottom],
        "size": [right - left, bottom - top], "dpi": window_dpi(info.hwnd),
        "revision": context.revision,
    }


def semantic_state(info: WindowInfo, elements: list[dict[str, Any]]) -> dict[str, Any]:
    compact = []
    for item in elements:
        compact.append({key: item.get(key) for key in ("role", "name", "rect", "value", "checked", "selected", "focused")
                        if item.get(key) not in (None, "", [])})
    return {"window": _window_state(info), "elements": compact}


def _memory_path(context: WindowContext) -> Path:
    identity = context.app_id or _hash({"name": context.app_name, "path": context.app_path})
    return memory_dir() / "apps" / f"{identity}.json"


def read_app_memory(context: WindowContext) -> dict[str, Any]:
    try:
        data = json.loads(_memory_path(context).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        if data.get("fingerprint") and data.get("fingerprint") != context.app_fingerprint:
            return {}
        return data
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def remember_success(context: WindowContext, action: dict[str, Any], before: str, after: str) -> None:
    if not before or not after or before == after:
        return
    if action.get("type") in ("click_point", "type"):
        return
    data = read_app_memory(context)
    data.update({
        "app_id": context.app_id, "app_name": context.app_name, "path": context.app_path,
        "fingerprint": context.app_fingerprint, "window_class": context.info().class_name,
    })
    successful = data.setdefault("successful_actions", [])
    signature = {key: action.get(key) for key in ("type", "text", "selector", "keys") if action.get(key)}
    record = next((item for item in successful if item.get("action") == signature), None)
    if record:
        record["successes"] = int(record.get("successes", 0)) + 1
        record["before"] = before
        record["after"] = after
    else:
        successful.append({"action": signature, "before": before, "after": after, "successes": 1, "failures": 0})
    data["successful_actions"] = sorted(successful, key=lambda item: -int(item.get("successes", 0)))[:50]
    path = _memory_path(context)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def memory_candidates(context: WindowContext, goal: str) -> list[dict[str, Any]]:
    needle = goal.casefold()
    results = []
    for item in read_app_memory(context).get("successful_actions", []):
        action = item.get("action", {})
        label = str(action.get("text") or action.get("selector", {}).get("name") or "")
        if label and (label.casefold() in needle or needle in label.casefold()):
            results.append(item)
    return results[:5]
