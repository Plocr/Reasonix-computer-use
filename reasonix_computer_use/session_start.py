"""Emit a stable, compact Reasonix SessionStart routing summary."""

from __future__ import annotations

import json
import sys

from .system_index import ensure_index


def summary() -> dict:
    index = ensure_index("session-start")
    system = index.get("system", {})
    folders = index.get("known_folders", {})
    return {
        "computer_use": "ready",
        "platform": system.get("platform", "Windows"),
        "language": system.get("language", "unknown"),
        "dpi_awareness": system.get("dpi_awareness", "unknown"),
        "displays": len(index.get("displays", [])),
        "known_folders": {name: value.get("path", "") for name, value in folders.items()
                          if name in ("桌面", "文档", "下载")},
        "routing": "desktop: computer_app -> computer_state -> computer_action; web page: chrome-devtools",
        "tools": ["computer_app", "computer_state", "computer_action", "computer_system"],
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(summary(), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
