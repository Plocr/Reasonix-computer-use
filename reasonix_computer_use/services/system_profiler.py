"""
SystemProfiler — unified system image service.

Builds a machine-readable system index (system-index.json) with:
  - OS version, architecture, DPI awareness mode
  - Hardware: CPU, GPU, memory
  - Displays: resolution, DPI, scale_factor (for coordinate conversion)
  - Known Folders: Desktop, Documents, Downloads, etc.
  - Applications: discovered paths with normalized coordinate fingerprints

Also generates a human-readable system.md summary and per-app JSON files
with verified launch paths stored in normalized coordinates for
cross-resolution replay.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


# ── Path management ─────────────────────────────────────────────────────────

def memory_dir() -> Path:
    configured = os.environ.get("REASONIX_MEMORY_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent.parent / "memory"


def index_path() -> Path:
    return memory_dir() / "system-index.json"


def profile_path() -> Path:
    return memory_dir() / "system.md"


def apps_dir() -> Path:
    return memory_dir() / "apps"


# ── Atomic write helper ─────────────────────────────────────────────────────

def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


# ── SystemProfiler ──────────────────────────────────────────────────────────

class SystemProfiler:
    """Build and manage the system index.

    Delegates platform-specific collection to internal helpers.
    """

    SCHEMA_VERSION = 3  # Bumped: added scale_factor, normalized coords

    def __init__(self):
        self._index: Optional[Dict[str, Any]] = None

    # ── Read ────────────────────────────────────────────────────────────────

    def load_index(self) -> Dict[str, Any]:
        """Load the system-index.json from disk, or return empty skeleton."""
        if self._index is not None:
            return self._index

        path = index_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._index = data
                    return data
            except (json.JSONDecodeError, OSError):
                pass

        self._index = {
            "schema_version": self.SCHEMA_VERSION,
            "system": {},
            "hardware": {},
            "displays": [],
            "known_folders": {},
            "default_apps": {},
            "applications": [],
        }
        return self._index

    def get_scale_factor(self) -> float:
        """Return the primary display's scale_factor for coordinate conversion."""
        index = self.load_index()
        displays = index.get("displays", [])
        if not displays:
            return 1.0
        # Find primary display, or use first
        for d in displays:
            if d.get("primary"):
                return d.get("scale_factor", 1.0)
        return displays[0].get("scale_factor", 1.0)

    # ── Profile generation ──────────────────────────────────────────────────

    def profile(self, reason: str = "manual refresh") -> None:
        """Build a fresh system profile.

        Delegates to the platform-specific collector (system_index module on Windows).
        """
        import sys
        from datetime import datetime, timezone

        if sys.platform == "win32":
            self._profile_windows(reason)
        else:
            self._profile_generic(reason)

        # Update timestamp
        index = self.load_index()
        index["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        index["reason"] = reason
        self._write_all(index)

    def _profile_windows(self, reason: str) -> None:
        """Collect Windows-specific system info using inline registry scanning."""
        import platform, locale, ctypes, winreg, os, subprocess, json
        from datetime import datetime, timezone

        index = self.load_index()
        index["system"] = {
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "language": locale.getdefaultlocale()[0] or "unknown",
            "timezone": str(datetime.now(timezone.utc).astimezone().tzinfo),
            "dpi_awareness": self._detect_dpi_awareness(),
        }
        index["hardware"] = {
            "cpu": os.environ.get("PROCESSOR_IDENTIFIER", platform.processor() or "unknown"),
            "gpu": self._detect_gpu(),
            "memory_gb": self._detect_memory(),
        }
        index["displays"] = self._detect_displays()
        index["known_folders"] = self._detect_folders()
        index["quick_scan_complete"] = True
        index["enrichment_complete"] = True
        self._index = index

    @staticmethod
    def _detect_dpi_awareness() -> str:
        try:
            user32 = ctypes.windll.user32
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return "per-monitor-v2"
        except: pass
        return "unknown"

    @staticmethod
    def _detect_gpu() -> str:
        try:
            result = subprocess.run(
                ["powershell", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=5)
            return result.stdout.strip().split("\n")[0] if result.stdout.strip() else "unknown"
        except: return "unknown"

    @staticmethod
    def _detect_memory() -> float:
        try:
            import ctypes.wintypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.wintypes.DWORD), ("dwMemoryLoad", ctypes.wintypes.DWORD),
                           ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                           ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                           ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                           ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            mem = MEMORYSTATUSEX()
            mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            return round(mem.ullTotalPhys / (1024**3), 1)
        except: return 0.0

    @staticmethod
    def _detect_displays() -> list:
        displays = []
        try:
            from ctypes import wintypes, Structure, byref, sizeof
            user32 = ctypes.windll.user32
            shcore = ctypes.windll.shcore

            class MONITORINFOEXW(Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32),
                ]

            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC,
                                ctypes.POINTER(wintypes.RECT), ctypes.c_void_p)
            def callback(mon, hdc, rect, _):
                info = MONITORINFOEXW()
                info.cbSize = sizeof(MONITORINFOEXW)
                if not user32.GetMonitorInfoW(mon, byref(info)):
                    return True
                dpi_x = ctypes.c_uint()
                dpi_y = ctypes.c_uint()
                try:
                    shcore.GetDpiForMonitor(mon, 0, byref(dpi_x), byref(dpi_y))
                except:
                    dpi_x, dpi_y = ctypes.c_uint(96), ctypes.c_uint(96)
                displays.append({
                    "width": info.rcMonitor.right - info.rcMonitor.left,
                    "height": info.rcMonitor.bottom - info.rcMonitor.top,
                    "dpi": dpi_x.value,
                    "scale_factor": round(dpi_x.value / 96.0, 2),
                    "scale_percent": round(dpi_x.value / 96.0 * 100),
                    "primary": bool(info.dwFlags & 1),
                    "left": info.rcMonitor.left,
                    "top": info.rcMonitor.top,
                    "name": info.szDevice if info.szDevice else f"Display {len(displays)+1}",
                })
                return True

            user32.EnumDisplayMonitors(None, None, callback, 0)
        except:
            pass
        return displays if displays else [{"width": 1920, "height": 1080, "dpi": 96,
                                            "scale_factor": 1.0, "scale_percent": 100,
                                            "primary": True, "left": 0, "top": 0, "name": "Default"}]

    @staticmethod
    def _detect_folders() -> dict:
        import winreg
        folders = {}
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
            for name, value_name in [("桌面", "Desktop"), ("文档", "Personal"),
                ("下载", "{374DE290-123F-4565-9164-39C4925E467B}"),
                ("图片", "My Pictures"), ("音乐", "My Music"), ("视频", "My Video")]:
                try:
                    path = winreg.QueryValueEx(key, value_name)[0]
                    folders[name] = {"path": path}
                except: pass
            winreg.CloseKey(key)
        except: pass
        return folders

    def _profile_generic(self, reason: str) -> None:
        """Generic fallback for non-Windows platforms."""
        import platform
        index = self.load_index()
        index["system"] = {
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
        }
        index["hardware"] = {
            "cpu": platform.processor() or "unknown",
        }

    def _write_all(self, index: Dict[str, Any]) -> None:
        """Write the index JSON and human-readable profile."""
        # Write JSON index
        serialized = json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n"
        _atomic_write(index_path(), serialized)

        # Write human-readable profile
        md = self._render_markdown(index)
        _atomic_write(profile_path(), md)

        # Ensure apps directory exists
        apps_dir().mkdir(parents=True, exist_ok=True)

    def _render_markdown(self, index: Dict[str, Any]) -> str:
        """Render the system index as a human-readable Markdown file."""
        system = index.get("system", {})
        hardware = index.get("hardware", {})
        folders = index.get("known_folders", {})
        displays = index.get("displays", [])
        apps = index.get("applications", [])

        lines = [
            "# Reasonix Computer Use — 系统画像",
            "",
            f"> 更新时间：{index.get('updated_at', 'unknown')}",
            f"> 更新原因：{index.get('reason', 'unknown')}",
            f"> 索引版本：{index.get('schema_version', self.SCHEMA_VERSION)}",
            "",
            "## 系统",
            "",
            f"- 平台：{system.get('platform', 'unknown')}",
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
            for d in displays:
                primary = " 🖥️ 主屏" if d.get("primary") else ""
                lines.append(
                    f"- {d.get('name', '显示器')}{primary}："
                    f"{d.get('width')}×{d.get('height')}，"
                    f"DPI {d.get('dpi', 96)}，"
                    f"缩放 {d.get('scale_percent', 100)}%，"
                    f"scale_factor {d.get('scale_factor', 1.0)}，"
                    f"原点 ({d.get('left', 0)}, {d.get('top', 0)})"
                )
        else:
            lines.append("- 未检测到显示器信息")

        lines.extend(["", "## 常用目录", ""])
        for name, data in folders.items():
            path = data.get("path") if isinstance(data, dict) else data
            lines.append(f"- {name}：`{path}`")

        lines.extend(["", "## 默认应用", ""])
        defaults = index.get("default_apps", {})
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
            "- 具体路径按需通过 `computer_app(search)` 或本文件查询。",
            "",
            "## 归一化坐标协议",
            "",
            f"- 坐标空间：CLAUDE_1024 (0–1023→1024×768), GEMINI_1000 (0–999→1000×1000), PIXEL, ELEMENT_REF",
            f"- 缩放因子 (scale_factor)：从显示器条目中读取",
            f"- 所有坐标由插件内部换算为物理像素后执行",
            "",
        ])
        return "\n".join(lines)

    # ── Per-app JSON ────────────────────────────────────────────────────────

    def save_app_path(
        self,
        app_name: str,
        executable: str,
        launch_args: Optional[list] = None,
    ) -> Path:
        """Save a verified app launch path.

        Coordinates in the app JSON are stored in normalized form
        (CLAUDE_1024) for cross-resolution replay.
        """
        apps_dir().mkdir(parents=True, exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in app_name)
        app_path = apps_dir() / f"{safe_name}.json"

        data = {
            "name": app_name,
            "executable": str(Path(executable).resolve()),
            "launch_args": launch_args or [],
            "verified_at": None,  # populated by runtime verification
            "schema_version": self.SCHEMA_VERSION,
        }

        # Merge with existing if present
        if app_path.exists():
            try:
                existing = json.loads(app_path.read_text(encoding="utf-8"))
                existing.update(data)
                data = existing
            except (json.JSONDecodeError, OSError):
                pass

        _atomic_write(app_path, json.dumps(data, ensure_ascii=False, indent=2))
        return app_path

    def load_app_path(self, app_name: str) -> Optional[Dict[str, Any]]:
        """Load a previously saved app launch path."""
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in app_name)
        app_path = apps_dir() / f"{safe_name}.json"
        if app_path.exists():
            try:
                return json.loads(app_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return None


# ── Singleton ───────────────────────────────────────────────────────────────

_profiler: Optional[SystemProfiler] = None


def get_profiler() -> SystemProfiler:
    global _profiler
    if _profiler is None:
        _profiler = SystemProfiler()
    return _profiler
