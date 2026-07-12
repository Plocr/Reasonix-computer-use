"""Cross-process guard against replayed text injection."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .system_profile import memory_dir


INPUT_GUARD_TTL_SECONDS = 600
_MAX_ENTRIES = 100


def _guard_path() -> Path:
    return memory_dir() / "runtime" / "input-guard.json"


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_entries(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        entries = value.get("entries", []) if isinstance(value, dict) else []
        return [item for item in entries if isinstance(item, dict)]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []


def _write_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".input-guard.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump({"version": 1, "entries": entries[-_MAX_ENTRIES:]}, stream,
                      ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def reserve_text_input(*, app_identity: str, window_class: str, state_hash: str,
                       target_ref: str, text: str, now: float | None = None,
                       ttl_seconds: int = INPUT_GUARD_TTL_SECONDS) -> bool:
    """Reserve a text injection signature, returning False for a recent replay.

    Only hashes are persisted. The reservation is written before input so a
    crashed or restarted MCP process cannot replay the same injection blindly.
    """
    timestamp = time.time() if now is None else now
    text_hash = _digest(text)
    signature = _digest({
        "app": app_identity.casefold(),
        "class": window_class.casefold(),
        "state": state_hash,
        "target": target_ref,
        "text": text_hash,
    })
    path = _guard_path()
    entries = [item for item in _read_entries(path)
               if timestamp - float(item.get("at", 0)) < ttl_seconds]
    if any(item.get("signature") == signature for item in entries):
        return False
    entries.append({"signature": signature, "text_hash": text_hash, "at": timestamp})
    _write_entries(path, entries)
    return True
