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
            "os_version": platform.version(),
            "os_release": platform.release(),
        }

        # ── Hardware: batch query via PowerShell for completeness ───────
        import subprocess, json as _json
        hw_data = self._query_hardware()
        index["hardware"] = {
            "cpu": hw_data.get("cpu", "unknown"),
            "cpu_cores": hw_data.get("cpu_cores", 0),
            "cpu_threads": hw_data.get("cpu_threads", 0),
            "gpus": hw_data.get("gpus", []),        # All GPUs (dedicated + integrated)
            "memory_gb": hw_data.get("memory_gb", 0),
            "memory_type": hw_data.get("memory_type", ""),
            "storage": hw_data.get("storage", []),   # All drives
            "motherboard": hw_data.get("motherboard", ""),
        }
        index["displays"] = self._detect_displays()
        index["known_folders"] = self._detect_folders()

        # ── Application discovery ──────────────────────────────────────
        apps = []
        apps.extend(self._scan_app_paths())
        apps.extend(self._scan_shortcuts())
        apps.extend(self._scan_start_apps())
        apps.extend(self._scan_running_windows())
        # Deduplicate by path
        seen = set()
        unique_apps = []
        for app in apps:
            path = app.get("path", "").lower()
            if path and path not in seen:
                seen.add(path)
                unique_apps.append(app)
            elif not path:
                unique_apps.append(app)
        index["applications"] = unique_apps
        index["app_count"] = len(unique_apps)
        index["app_categories"] = {
            "registry": sum(1 for a in unique_apps if a.get("source") == "registry"),
            "start_menu": sum(1 for a in unique_apps if a.get("source") == "start_menu"),
            "uwp": sum(1 for a in unique_apps if a.get("source") == "uwp"),
            "running": sum(1 for a in unique_apps if a.get("source") == "running"),
        }
        index["quick_scan_complete"] = True
        index["enrichment_complete"] = True
        self._index = index

    @staticmethod
    def _detect_dpi_awareness() -> str:
        import ctypes
        try:
            user32 = ctypes.windll.user32
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return "per-monitor-v2"
        except:
            pass
        return "unknown"

    @staticmethod
    def _detect_gpu() -> str:
        import subprocess
        try:
            result = subprocess.run(
                ["powershell", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=5)
            return result.stdout.strip().split("\n")[0] if result.stdout.strip() else "unknown"
        except:
            return "unknown"

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
            import ctypes
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

            _MonCallback = ctypes.WINFUNCTYPE(
                ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC,
                ctypes.POINTER(wintypes.RECT), ctypes.c_void_p)

            @_MonCallback
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

    # ── Hardware query ───────────────────────────────────────────────────────

    @staticmethod
    def _query_hardware() -> dict:
        """Batch-query all hardware info via a single PowerShell invocation."""
        import subprocess, json
        script = r'''
$cpu = Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors
$gpus = Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion
$mem = Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity,Speed
$drives = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID,Size,FreeSpace,FileSystem
$board = Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product
$result = @{}
if ($cpu) { $result.cpu = $cpu[0].Name; $result.cpu_cores = $cpu[0].NumberOfCores; $result.cpu_threads = $cpu[0].NumberOfLogicalProcessors }
if ($gpus) { $result.gpus = @(); foreach ($g in $gpus) { $r = [math]::Round($g.AdapterRAM / 1GB, 1); $result.gpus += @{"name"=$g.Name; "vram_gb"=$r; "driver"=$g.DriverVersion} } }
if ($mem) { $t=0; foreach ($m in $mem) {$t+=[long]$m.Capacity}; $result.memory_gb = [math]::Round($t/1GB,1); $result.memory_speed = "$($mem[0].Speed)MHz" }
if ($drives) { $result.storage = @(); foreach ($d in $drives) {$result.storage += @{"drive"=$d.DeviceID; "size_gb"=[math]::Round([long]$d.Size/1GB); "free_gb"=[math]::Round([long]$d.FreeSpace/1GB); "fs"=$d.FileSystem}} }
if ($board) { $result.motherboard = "$($board[0].Manufacturer) $($board[0].Product)" }
return $result | ConvertTo-Json -Compress
'''
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                    capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
        except:
            pass
        return {"cpu": "unknown", "gpus": [], "memory_gb": 0, "storage": []}

    # ── Application discovery ────────────────────────────────────────────────

    @staticmethod
    def _scan_app_paths() -> list[dict]:
        """Scan registry App Paths for installed applications."""
        import winreg, os
        apps = []
        roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
        ]
        for hive, key_path in roots:
            try:
                key = winreg.OpenKey(hive, key_path)
                try:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            subkey = winreg.OpenKey(key, subkey_name)
                            try:
                                path = winreg.QueryValueEx(subkey, "")[0]
                                if path and os.path.isfile(path):
                                    import pathlib
                                    apps.append({
                                        "name": pathlib.Path(subkey_name).stem,
                                        "path": path,
                                        "source": "registry",
                                        "confidence": 0.9,
                                    })
                            except: pass
                            winreg.CloseKey(subkey)
                        except: pass
                finally:
                    winreg.CloseKey(key)
            except: pass
        return apps

    @staticmethod
    def _scan_shortcuts() -> list[dict]:
        """Scan Start Menu and Desktop for .lnk shortcuts, resolving real targets."""
        import os, subprocess, json
        apps = []
        folders = [
            os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ.get("USERPROFILE", ""), "Desktop") if os.environ.get("USERPROFILE") else "",
        ]
        # Collect all .lnk paths
        lnk_paths = []
        for folder in folders:
            if not folder or not os.path.isdir(folder):
                continue
            for root, _dirs, files in os.walk(folder):
                for f in files:
                    if f.lower().endswith(".lnk"):
                        lnk_paths.append(os.path.join(root, f))

        if not lnk_paths:
            return apps

        # Resolve all .lnk targets via PowerShell (much faster than per-call COM)
        import tempfile
        script = r'''
$output = @()
foreach ($lnk in @(%s)) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut($lnk)
        $target = $sc.TargetPath
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null
        if ($target -and (Test-Path $target)) {
            $name = [System.IO.Path]::GetFileNameWithoutExtension($lnk)
            $output += @{"name" = $name; "target" = $target}
        }
    } catch {}
}
return $output | ConvertTo-Json -Compress
''' % ';'.join("'%s'" % p.replace("'", "''") for p in lnk_paths)

        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                entries = data if isinstance(data, list) else [data]
                for entry in entries:
                    name = entry.get("name", "")
                    target = entry.get("target", "")
                    if name and target:
                        import pathlib
                        apps.append({
                            "name": name,
                            "path": target,  # Real EXE path, not .lnk
                            "source": "start_menu",
                            "confidence": 0.8,
                        })
        except: pass
        return apps

    @staticmethod
    def _scan_start_apps() -> list[dict]:
        """Scan UWP/Store apps via PowerShell Get-StartApps."""
        import subprocess, json
        apps = []
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json"],
                capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                entries = data if isinstance(data, list) else [data]
                for entry in entries:
                    name = entry.get("Name", "")
                    app_id = entry.get("AppID", "")
                    if name:
                        apps.append({
                            "name": name,
                            "path": f"shell:AppsFolder\\{app_id}" if app_id else "",
                            "source": "uwp",
                            "app_id": app_id,
                            "confidence": 0.6,
                        })
        except: pass
        return apps

    @staticmethod
    def _scan_running_windows() -> list[dict]:
        """Discover currently running application windows."""
        import ctypes, os
        apps = []
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            _CallbackType = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

            @_CallbackType
            def callback(hwnd, _):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length < 2:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = buffer.value
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                handle = kernel32.OpenProcess(0x1000, False, pid.value)
                path = ""
                if handle:
                    try:
                        sz = wintypes.DWORD(32768)
                        buf = ctypes.create_unicode_buffer(sz.value)
                        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(sz)):
                            path = buf.value
                    finally:
                        kernel32.CloseHandle(handle)
                if path and os.path.isfile(path):
                    import pathlib
                    apps.append({
                        "name": pathlib.Path(path).stem,
                        "path": path,
                        "title": title,
                        "source": "running",
                        "confidence": 0.8,
                        "pid": pid.value,
                    })
                return True

            user32.EnumWindows(callback, 0)
        except: pass
        return apps

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
        """Render the system index as a human-readable Markdown summary (outline)."""
        system = index.get("system", {})
        hardware = index.get("hardware", {})
        folders = index.get("known_folders", {})
        displays = index.get("displays", [])
        apps = index.get("applications", [])
        cats = index.get("app_categories", {})
        app_count = index.get("app_count", 0)

        lines = [
            "# Reasonix Computer Use — 系统画像",
            "",
            f"> 更新时间：{index.get('updated_at', 'unknown')}",
            f"> 索引版本：{index.get('schema_version', 3)}",
            "",
            "## 系统概要",
            "",
            f"- **操作系统**：{system.get('platform', 'unknown')}",
            f"- **版本**：{system.get('os_release', '')} ({system.get('os_version', '')})",
            f"- **架构**：{system.get('architecture', 'unknown')}",
            f"- **语言**：{system.get('language', 'unknown')}",
            f"- **DPI 感知**：{system.get('dpi_awareness', 'unknown')}",
            "",
            "## 硬件",
            "",
            f"- **CPU**：{hardware.get('cpu', 'unknown')}",
            f"- **核心/线程**：{hardware.get('cpu_cores', '?')} 核 / {hardware.get('cpu_threads', '?')} 线程",
        ]
        gpus = hardware.get("gpus", [])
        if gpus:
            lines.append(f"- **GPU ({len(gpus)} 个)**：")
            for gpu in gpus:
                vram = gpu.get("vram_gb", 0)
                vram_str = f"（{vram}GB 显存）" if vram else ""
                lines.append(f"  - {gpu.get('name', '?')}{vram_str}")
        else:
            lines.append("- **GPU**：未检测到")
        lines.extend([
            f"- **内存**：{hardware.get('memory_gb', '?')} GB {hardware.get('memory_speed', '')}",
            f"- **主板**：{hardware.get('motherboard', 'unknown')}",
            "",
            "### 存储设备",
            "",
        ])
        for disk in hardware.get("storage", []):
            used = disk.get("size_gb", 0) - disk.get("free_gb", 0)
            lines.append(
                f"- **{disk.get('drive', '?')}**：{disk.get('size_gb', 0)} GB"
                f"（已用 {used} GB / 剩余 {disk.get('free_gb', 0)} GB, {disk.get('fs', '?')}）"
            )
        lines.extend(["", "## 显示器", ""])
        for d in displays:
            primary = " 🖥️ **主屏**" if d.get("primary") else ""
            lines.append(
                f"- {d.get('name', '显示器')}{primary}："
                f"{d.get('width')}×{d.get('height')} @ {d.get('dpi', 96)} DPI"
                f"（缩放 {d.get('scale_percent', 100)}%，scale_factor={d.get('scale_factor', 1.0)}）"
            )

        lines.extend(["", "## 常用目录", ""])
        for name, data in folders.items():
            path = data.get("path") if isinstance(data, dict) else data
            lines.append(f"- **{name}**：`{path}`")

        lines.extend(["", "## 已发现应用", ""])
        lines.append(f"- **总数**：{app_count}")
        if cats:
            lines.append(f"- 注册表 App Paths：{cats.get('registry', 0)}")
            lines.append(f"- 开始菜单快捷方式：{cats.get('start_menu', 0)}")
            lines.append(f"- UWP/Store 应用：{cats.get('uwp', 0)}")
            lines.append(f"- 当前运行中：{cats.get('running', 0)}")
        lines.append("")
        lines.append("> 完整应用列表见 `system-index.json`，按需通过 `computer_app(search)` 查询。")
        lines.append("")

        if apps:
            lines.append("### 应用示例")
            lines.append("")
            # Show a few interesting examples
            shown = 0
            for app in apps:
                if app.get("source") in ("running", "registry") and shown < 8:
                    lines.append(f"- `{app.get('name', '?')}` → {app.get('path', '?')}")
                    shown += 1
            lines.append("")
            if len(apps) > shown:
                lines.append(f"> 还有 {len(apps) - shown} 个应用未在此列出。")

        lines.extend([
            "",
            "## 坐标协议",
            "",
            "所有坐标使用归一化空间（CLAUDE_1024 / GEMINI_1000 / PIXEL / ELEMENT_REF），",
            "插件内部通过 `system-index.json` 中的 `scale_factor` 换算为物理像素。",
            "",
            f"- 当前主屏 scale_factor：{displays[0].get('scale_factor', 1.0) if displays else 1.0}",
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
