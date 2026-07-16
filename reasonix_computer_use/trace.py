"""Privacy-preserving task traces for capability diagnostics and replay."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from . import __version__
from .system_profile import memory_dir, read_index


SCHEMA_VERSION = 1
MAX_TRACES = 50
MAX_EVENTS = 300
MAX_TRACE_BYTES = 256 * 1024
SAFE_STRING_KEYS = {
    "type", "operation", "action", "status", "code", "source", "strategy", "method",
    "role", "class", "revision", "window_id", "trace_id", "mode", "direction", "keys",
    "progress", "next_hint", "app_id", "app_name", "platform", "language", "event",
}
PATH_KEYS = {"path", "destination", "image_path", "file", "root", "cwd"}
TEXT_KEYS = {"text", "value", "name", "title", "prompt", "goal", "clipboard", "query"}
SECRET_MARKERS = ("password", "passwd", "secret", "token", "authorization", "cookie", "captcha", "验证码", "密码")


def trace_dir() -> Path:
    return memory_dir() / "traces"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]


def _known_folder_tokens() -> list[tuple[str, str]]:
    index = read_index() or {}
    result = []
    aliases = {"桌面": "desktop", "文档": "documents", "下载": "downloads",
               "图片": "pictures", "音乐": "music", "视频": "videos"}
    for name, item in index.get("known_folders", {}).items():
        value = item.get("path") if isinstance(item, dict) else item
        if value:
            result.append((os.path.normcase(str(value)), f"<{aliases.get(name, 'known-folder')}>"))
    result.sort(key=lambda pair: len(pair[0]), reverse=True)
    return result


def redact_path(value: str) -> str:
    normalized = os.path.normcase(value)
    for prefix, token in _known_folder_tokens():
        if normalized == prefix:
            return token
        if normalized.startswith(prefix + os.sep):
            return token + os.sep + Path(value).name
    home = os.path.normcase(str(Path.home()))
    if normalized == home or normalized.startswith(home + os.sep):
        return "~" + (os.sep + Path(value).name if normalized != home else "")
    if os.path.isabs(value):
        return f"<external>{os.sep}{Path(value).name}"
    return value[:160]


def _redacted_text(value: str, *, test_mode: bool) -> Any:
    if test_mode and value.startswith("SYNTHETIC_"):
        return value
    return {"kind": "redacted_text", "length": len(value), "sha256": _digest(value)}


def sanitize(value: Any, *, key: str = "", test_mode: bool = False, depth: int = 0) -> Any:
    if depth > 8:
        return "<depth-limit>"
    lowered = key.casefold()
    if any(marker in lowered for marker in SECRET_MARKERS):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k)[:80]: sanitize(v, key=str(k), test_mode=test_mode, depth=depth + 1)
                for k, v in list(value.items())[:100]}
    if isinstance(value, (list, tuple)):
        return [sanitize(item, key=key, test_mode=test_mode, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, str):
        if lowered in PATH_KEYS or (len(value) > 2 and (":\\" in value or value.startswith("/"))):
            return redact_path(value)
        if lowered in TEXT_KEYS or lowered not in SAFE_STRING_KEYS:
            return _redacted_text(value, test_mode=test_mode)
        return value[:200]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redacted_text(str(value), test_mode=test_mode)


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _environment() -> dict[str, Any]:
    index = read_index() or {}
    system = index.get("system", {})
    return {
        "platform": platform.system().casefold(),
        "language": system.get("language", "unknown"),
        "display_count": len(index.get("displays", [])),
        "dpi_awareness": system.get("dpi_awareness", "unknown"),
        "known_folder_count": len(index.get("known_folders", {})),
    }


def _prune() -> None:
    directory = trace_dir()
    if not directory.exists():
        return
    files = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for path in files[MAX_TRACES:]:
        try:
            path.unlink()
        except OSError:
            pass


def start_trace(kind: str = "computer-task", *, test_mode: bool = False,
                metadata: dict[str, Any] | None = None) -> str:
    trace_id = f"t-{uuid.uuid4().hex[:16]}"
    now = time.time()
    document = {
        "schema_version": SCHEMA_VERSION,
        "trace_id": trace_id,
        "plugin_version": __version__,
        "kind": kind,
        "test_mode": bool(test_mode),
        "created_at": now,
        "updated_at": now,
        "events": [],
    }
    _atomic_json(trace_dir() / f"{trace_id}.json", document)
    record_event(trace_id, "task_start", {"metadata": metadata or {}})
    record_event(trace_id, "environment", _environment())
    _prune()
    return trace_id


def read_trace(trace_id: str) -> dict[str, Any] | None:
    suffix = trace_id[2:] if trace_id.startswith("t-") else ""
    if not suffix or any(char not in "0123456789abcdef" for char in suffix):
        return None
    try:
        value = json.loads((trace_dir() / f"{trace_id}.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def record_event(trace_id: str, event: str, data: dict[str, Any] | None = None) -> bool:
    document = read_trace(trace_id)
    if not document:
        return False
    test_mode = bool(document.get("test_mode"))
    item = {"event": event, "at": time.time(),
            "data": sanitize(data or {}, test_mode=test_mode)}
    events = document.setdefault("events", [])
    events.append(item)
    if len(events) > MAX_EVENTS:
        del events[:len(events) - MAX_EVENTS]
    document["updated_at"] = item["at"]
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    while len(encoded) > MAX_TRACE_BYTES and len(events) > 2:
        del events[1]
        encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _atomic_json(trace_dir() / f"{trace_id}.json", document)
    return True


def finish_trace(trace_id: str, status: str, metrics: dict[str, Any] | None = None) -> bool:
    return record_event(trace_id, "task_end", {"status": status, "metrics": metrics or {}})


def list_traces(limit: int = 20) -> list[dict[str, Any]]:
    result = []
    files = sorted(trace_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True) if trace_dir().exists() else []
    for path in files[:max(1, min(int(limit), MAX_TRACES))]:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            result.append({"trace_id": item.get("trace_id"), "kind": item.get("kind"),
                           "created_at": item.get("created_at"), "updated_at": item.get("updated_at"),
                           "events": len(item.get("events", [])), "bytes": path.stat().st_size})
        except (OSError, json.JSONDecodeError):
            continue
    return result


def export_trace(trace_id: str, destination: str) -> str:
    document = read_trace(trace_id)
    if not document:
        raise FileNotFoundError(trace_id)
    target = Path(destination).expanduser()
    if not target.is_absolute() or not target.parent.is_dir():
        raise ValueError("destination must be an absolute path with an existing parent")
    _atomic_json(target, document)
    return str(target)
