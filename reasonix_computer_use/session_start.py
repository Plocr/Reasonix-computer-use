"""Emit a stable, compact Reasonix SessionStart routing summary."""

from __future__ import annotations

import json
import sys

from .system_index import ensure_index
from .environment_setup import environment_status


def summary() -> dict:
    index = ensure_index("session-start")
    system = index.get("system", {})
    folders = index.get("known_folders", {})
    environment = environment_status()
    return {
        "computer_use": "ready" if environment.get("ready") else "setup_required",
        "platform": system.get("platform", "Windows"),
        "language": system.get("language", "unknown"),
        "dpi_awareness": system.get("dpi_awareness", "unknown"),
        "displays": len(index.get("displays", [])),
        "known_folders": {name: value.get("path", "") for name, value in folders.items()
                          if name in ("桌面", "文档", "下载")},
        "routing": "desktop: computer_app -> computer_state -> computer_action; web page: chrome-devtools",
        "tools": ["computer_app", "computer_state", "computer_action", "computer_system"],
        "environment": {key: environment.get(key) for key in ("status", "ready", "missing")},
        "setup_hint": (None if environment.get("ready") else
                       "经用户确认后调用 computer_system(operation=setup, params.confirmed=true)，随后调用 setup_status(wait_seconds=20)；禁止 Shell sleep"),
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(summary(), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
