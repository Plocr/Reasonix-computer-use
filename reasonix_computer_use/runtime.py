"""Window state, adaptive fallback and verified application memory."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .system_profile import memory_dir
from .windows import WindowInfo, get_window_info, list_windows, user32, window_dpi


STRATEGIES = ("memory", "uia", "ocr", "visual")
RUNTIME_STATE_TTL_SECONDS = 300
RUNTIME_ELEMENT_KEYS = (
    "ref", "role", "name", "rect", "actions", "id", "automation_id", "class", "class_name",
    "confidence", "action", "focused", "selected", "coordinate_space",
)


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:12]


def _runtime_state_dir() -> Path:
    configured = os.environ.get("REASONIX_RUNTIME_STATE_DIR")
    if configured:
        return Path(configured)
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "Reasonix" / "computer-use" / "window-state"


def _runtime_state_path(window_id: str) -> Path:
    return _runtime_state_dir() / f"{hashlib.sha256(window_id.encode('utf-8')).hexdigest()[:24]}.json"


def _runtime_identity(info: WindowInfo) -> str:
    return _hash({"hwnd": info.hwnd, "pid": info.pid, "class": info.class_name,
                  "path": info.process_path.casefold()})


def _safe_runtime_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe = []
    for element in elements[:80]:
        item = {key: element.get(key) for key in RUNTIME_ELEMENT_KEYS
                if element.get(key) not in (None, "", [])}
        if "name" in item:
            item["name"] = str(item["name"])[:160]
        safe.append(item)
    return safe


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
    references: dict[str, dict[str, Any]] = field(default_factory=dict)
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
    restored: bool = False

    def info(self) -> WindowInfo:
        if user32.IsWindow(self.hwnd):
            try:
                return get_window_info(self.hwnd)
            except (OSError, ValueError):
                pass
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
        self.restored = False
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
            self.references.clear()
        self.source = source
        if elements is not None:
            self.elements = elements
            for element in elements:
                ref = str(element.get("ref", ""))
                if ref:
                    self.references[ref] = element
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

    def begin_task(self) -> None:
        self.failure_hash = ""
        self.failure_count = 0
        self.strategy_level = 1
        self.action_signatures.clear()
        self.invalid_action_count = 0
        self.no_progress_count = 0
        self.hard_blocked = False
        self.state_reads_without_action = 0
        self.focused_ref = ""


class WindowRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._contexts: dict[str, WindowContext] = {}

    @staticmethod
    def persist(context: WindowContext) -> None:
        """Persist only short-lived perception metadata across stdio restarts."""
        if not context.window_id.startswith("w-"):
            return
        try:
            info = context.info()
            path = _runtime_state_path(context.window_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "updated_at": time.time(),
                "identity": _runtime_identity(info),
                "revision_no": context.revision_no,
                "revision": context.revision,
                "state_hash": context.state_hash,
                "source_hashes": context.source_hashes,
                "source": context.source,
                "elements": _safe_runtime_elements(context.elements),
                "references": _safe_runtime_elements(list(context.references.values())),
                "image_hash": context.image_hash,
                "failure_hash": context.failure_hash,
                "failure_count": context.failure_count,
                "strategy_level": context.strategy_level,
                "action_signatures": sorted(context.action_signatures),
                "invalid_action_count": context.invalid_action_count,
                "no_progress_count": context.no_progress_count,
                "hard_blocked": context.hard_blocked,
                "state_reads_without_action": context.state_reads_without_action,
            }
            handle, temporary = tempfile.mkstemp(prefix=".window-state.", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except (OSError, TypeError, ValueError):
            pass

    @staticmethod
    def _restore(context: WindowContext, info: WindowInfo) -> None:
        path = _runtime_state_path(context.window_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(payload, dict)
                    or time.time() - float(payload.get("updated_at", 0)) > RUNTIME_STATE_TTL_SECONDS
                    or payload.get("identity") != _runtime_identity(info)
                    or payload.get("source") not in ("uia", "ocr", "memory", "visual")):
                return
            elements = payload.get("elements", [])
            if not isinstance(elements, list):
                return
            context.revision_no = max(1, int(payload.get("revision_no", 1)))
            context.revision = str(payload.get("revision") or context.revision)
            context.state_hash = str(payload.get("state_hash") or context.state_hash)
            hashes = payload.get("source_hashes", {})
            context.source_hashes = hashes if isinstance(hashes, dict) else {}
            context.source = str(payload.get("source"))
            context.elements = _safe_runtime_elements([item for item in elements if isinstance(item, dict)])
            context.image_hash = str(payload.get("image_hash", ""))
            context.failure_hash = str(payload.get("failure_hash", ""))
            context.failure_count = max(0, int(payload.get("failure_count", 0)))
            context.strategy_level = max(1, min(int(payload.get("strategy_level", 1)), len(STRATEGIES) - 1))
            signatures = payload.get("action_signatures", [])
            context.action_signatures = {str(item) for item in signatures[:100]} if isinstance(signatures, list) else set()
            context.invalid_action_count = max(0, int(payload.get("invalid_action_count", 0)))
            context.no_progress_count = max(0, int(payload.get("no_progress_count", 0)))
            context.hard_blocked = bool(payload.get("hard_blocked"))
            context.state_reads_without_action = max(0, int(payload.get("state_reads_without_action", 0)))
            references = payload.get("references", context.elements)
            if isinstance(references, list):
                context.references = {str(item.get("ref")): item for item in _safe_runtime_elements(
                    [item for item in references if isinstance(item, dict)]) if item.get("ref")}
            context.restored = True
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return

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
            # The MCP host may restart the stdio server between tool calls.
            # Encode the HWND so a returned id can be recovered in a new process.
            app_id = str((app or {}).get("id", ""))
            suffix = f"-{app_id}" if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", app_id) else ""
            window_id = f"w-{info.hwnd:x}-{info.pid:x}{suffix}"
            context = WindowContext(
                window_id=window_id, hwnd=info.hwnd, app_id=str((app or {}).get("id", "")),
                app_name=str((app or {}).get("name", "")), app_path=info.process_path or str((app or {}).get("path", "")),
                app_fingerprint=_hash({key: (app or {}).get(key, "") for key in ("id", "version", "sha256")}),
                owner_pid=info.pid,
            )
            context.update(_window_state(info), "window")
            self._restore(context, info)
            self._contexts[window_id] = context
            return context

    def get(self, window_id: str) -> WindowContext:
        with self._lock:
            if window_id in self._contexts:
                return self._contexts[window_id]
        if str(window_id).lower().startswith("0x") or str(window_id).isdigit():
            return self.register(get_window_info(int(str(window_id), 0)))
        match = re.fullmatch(r"w-([0-9a-fA-F]+)-([0-9a-fA-F]+)(?:-([A-Za-z0-9._-]{1,80}))?",
                             str(window_id))
        if match:
            app_id = match.group(3) or ""
            try:
                info = get_window_info(int(match.group(1), 16))
                if info.pid == int(match.group(2), 16):
                    return self.register(info, {"id": app_id} if app_id else None)
            except (OSError, ValueError):
                pass
            recovered = self._recover_app(app_id) if app_id else None
            if recovered:
                info, app = recovered
                return self.register(info, app)
        raise KeyError(window_id)

    @staticmethod
    def _recover_app(app_id: str, timeout: float = 5.0) -> tuple[WindowInfo, dict[str, Any]] | None:
        """Follow launchers that replace their initial HWND or process."""
        from .system_index import find_app

        app = find_app(app_id)
        if not app:
            return None
        target = str(app.get("path") or app.get("launch_target") or "").casefold()
        stem = Path(target).stem.casefold() if target and not target.startswith("shell:") else ""
        name = str(app.get("name", "")).casefold()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            candidates = [item for item in list_windows()
                          if item.rect[0] > -30000 and item.rect[1] > -30000
                          and item.rect[2] - item.rect[0] >= 50 and item.rect[3] - item.rect[1] >= 30]
            exact = [item for item in candidates if target and item.process_path.casefold() == target]
            executable = [item for item in candidates
                          if stem and Path(item.process_path).stem.casefold() == stem]
            titled = [item for item in candidates if name and item.title.casefold() == name]
            ranked = exact or executable or titled
            if ranked:
                return max(ranked, key=lambda item: ((item.rect[2] - item.rect[0]) *
                                                     (item.rect[3] - item.rect[1]))), app
            time.sleep(0.15)
        return None

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
