"""Human-readable system profile generated from the authoritative index."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


PROFILE_FILENAME = "system.md"
INDEX_FILENAME = "system-index.json"


def memory_dir() -> Path:
    configured = os.environ.get("REASONIX_MEMORY_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / "memory"


def profile_path() -> Path:
    return memory_dir() / PROFILE_FILENAME


def index_path() -> Path:
    return memory_dir() / INDEX_FILENAME


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_profile() -> str | None:
    try:
        return profile_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def read_index() -> dict[str, Any] | None:
    try:
        value = json.loads(index_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def render_profile(index: dict[str, Any]) -> str:
    system = index.get("system", {})
    hardware = index.get("hardware", {})
    folders = index.get("known_folders", {})
    displays = index.get("displays", [])
    apps = index.get("applications", [])
    lines = [
        "# Reasonix Computer Use 系统画像",
        "",
        f"> 更新时间：{index.get('updated_at', 'unknown')}",
        f"> 更新原因：{index.get('reason', 'unknown')}",
        f"> 索引版本：{index.get('schema_version', 1)}",
        "",
        "## 系统",
        "",
        f"- Windows：{system.get('platform', 'unknown')}",
        f"- 架构：{system.get('architecture', 'unknown')}",
        f"- 语言：{system.get('language', 'unknown')}",
        f"- 时区：{system.get('timezone', 'unknown')}",
        f"- DPI 感知：{system.get('dpi_awareness', 'unknown')}",
        "",
        "## 硬件",
        "",
        f"- CPU：{hardware.get('cpu', 'unknown')}",
        f"- GPU：{hardware.get('gpu', 'unknown')}",
        f"- 内存：{hardware.get('memory_gb', 'unknown')} GB",
        "",
        "## 显示器",
        "",
    ]
    if displays:
        for display in displays:
            lines.append(
                f"- {display.get('name', '显示器')}：{display.get('width')}x{display.get('height')}，"
                f"DPI {display.get('dpi', 96)}，缩放 {display.get('scale_percent', 100)}%，"
                f"原点 ({display.get('left', 0)},{display.get('top', 0)})"
            )
    else:
        lines.append("- 未检测到显示器信息")
    lines.extend(["", "## 常用目录", ""])
    for name, data in folders.items():
        path = data.get("path") if isinstance(data, dict) else data
        lines.append(f"- {name}：`{path}`")
    defaults = index.get("default_apps", {})
    lines.extend(["", "## 默认应用", ""])
    if defaults:
        lines.extend(f"- {name}：{value}" for name, value in defaults.items())
    else:
        lines.append("- 尚未检测")
    lines.extend([
        "",
        "## 应用索引",
        "",
        f"- 已发现应用：{len(apps)}",
        f"- 快速扫描完成：{'是' if index.get('quick_scan_complete') else '否'}",
        f"- 后台补充完成：{'是' if index.get('enrichment_complete') else '否'}",
        "- 具体路径按需通过 `computer_app(search)` 或 `computer_system(profile)` 查询，不在此文件展开。",
        "",
    ])
    return "\n".join(lines)


def write_profile_and_index(index: dict[str, Any]) -> tuple[str, str]:
    """Replace both profile files; the JSON index remains authoritative."""
    serialized = json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n"
    _atomic_write(index_path(), serialized)
    _atomic_write(profile_path(), render_profile(index))
    return str(profile_path()), str(index_path())


def remember_executable(name: str, executable: str) -> None:
    """Compatibility helper used by legacy internal application discovery."""
    index = read_index() or {"schema_version": 2, "applications": []}
    normalized = str(Path(executable).resolve())
    applications = index.setdefault("applications", [])
    if not any(str(item.get("path", "")).casefold() == normalized.casefold() for item in applications):
        applications.append({
            "id": Path(normalized).stem.casefold(), "name": name, "path": normalized,
            "launch_target": normalized, "source": "learned", "confidence": 1.0,
        })
        write_profile_and_index(index)


def write_profile(applications_markdown: str, reason: str) -> str:
    """Legacy shim. New code writes structured data through system_index."""
    index = read_index() or {"schema_version": 2, "applications": []}
    index["reason"] = reason
    write_profile_and_index(index)
    return str(profile_path())
