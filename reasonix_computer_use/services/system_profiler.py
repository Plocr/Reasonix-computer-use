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
from shutil import which
from typing import Any, Dict, Optional

try:
    import mss  # type: ignore  # optional: Linux/X11 screenshots
except ImportError:
    mss = None  # type: ignore

from .. import __version__


# ── Path management ─────────────────────────────────────────────────────────

def memory_dir() -> Path:
    configured = os.environ.get("REASONIX_MEMORY_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent.parent / "memory"


def reasonix_global_memory_dir() -> Optional[Path]:
    """Return Reasonix's global memory directory (for AI-visible memories)."""
    import os as _os
    # Reasonix stores global memories at %APPDATA%/reasonix/memory/
    base = _os.environ.get("APPDATA") or _os.path.join(_os.path.expanduser("~"), "AppData", "Roaming")
    path = Path(base) / "reasonix" / "memory" / "global"
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    return path if path.exists() else None


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
        elif sys.platform == "linux":
            self._profile_linux(reason)
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

        try:
            language = locale.getlocale()[0] or "unknown"
        except (ValueError, TypeError, AttributeError):
            language = "unknown"
        index = self.load_index()
        index["system"] = {
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "language": language,
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
        apps.extend(self._scan_uninstall_keys())
        apps.extend(self._scan_shortcuts())
        apps.extend(self._scan_start_apps())
        apps.extend(self._scan_running_windows())
        apps.extend(self._scan_drive_roots())
        apps.extend(self._scan_program_files())
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
            "uninstall": sum(1 for a in unique_apps if a.get("source") == "uninstall"),
            "start_menu": sum(1 for a in unique_apps if a.get("source") == "start_menu"),
            "uwp": sum(1 for a in unique_apps if a.get("source") == "uwp"),
            "running": sum(1 for a in unique_apps if a.get("source") == "running"),
            "drive_scan": sum(1 for a in unique_apps if a.get("source") == "drive_scan"),
            "program_files": sum(1 for a in unique_apps if a.get("source") == "program_files"),
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
        if displays:
            return displays
        # NO silent fake default: a wrong 1920x1080/1.0 would corrupt every
        # normalized-coordinate conversion on a 125%/150% scaled system.
        # Mark the entry as undetected so the coordinate layer can refuse it.
        return [{"width": 0, "height": 0, "dpi": 0, "scale_factor": 0.0,
                 "scale_percent": 0, "primary": True, "left": 0, "top": 0,
                 "name": "Undetected", "detected": False}]

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
    def _scan_uninstall_keys() -> list[dict]:
        """Scan registry Uninstall keys for installed software (most comprehensive source)."""
        import winreg, os
        apps = []
        roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        _SKIP_EXE = {"uninstall", "uninst", "update", "setup", "crash", "helper", "installer"}
        for hive, key_path in roots:
            try:
                key = winreg.OpenKey(hive, key_path)
                try:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            subkey = winreg.OpenKey(key, subkey_name)
                            try:
                                name = ""
                                location = ""
                                icon = ""
                                try:
                                    name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                                except FileNotFoundError:
                                    pass
                                try:
                                    location = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                                except FileNotFoundError:
                                    pass
                                try:
                                    icon = winreg.QueryValueEx(subkey, "DisplayIcon")[0]
                                    if "," in icon:
                                        icon = icon.split(",")[0].strip()
                                except FileNotFoundError:
                                    pass
                                if not name:
                                    winreg.CloseKey(subkey)
                                    continue
                                exe_path = ""
                                if icon and os.path.isfile(icon):
                                    exe_path = icon
                                if not exe_path and location and os.path.isdir(location):
                                    for exe_name in os.listdir(location):
                                        base = os.path.splitext(exe_name)[0].lower()
                                        if exe_name.lower().endswith('.exe') and base not in _SKIP_EXE:
                                            exe_path = os.path.join(location, exe_name)
                                            break
                                if not exe_path and not location:
                                    continue
                                apps.append({
                                    "name": name,
                                    "path": exe_path or location,
                                    "source": "uninstall",
                                    "confidence": 0.9,
                                })
                            except Exception: pass
                            winreg.CloseKey(subkey)
                        except Exception: pass
                finally:
                    winreg.CloseKey(key)
            except Exception: pass
        return apps

    @staticmethod
    def _scan_program_files() -> list[dict]:
        """Scan Program Files directories on all drives for .exe files."""
        import os
        apps = []
        _SKIP_EXE = {"uninstall", "uninst", "update", "setup", "crash", "helper",
                      "installer", "repair", "config", "diagnostic"}
        for letter in "CDEFGH":
            for pf_dir in ("Program Files", "Program Files (x86)"):
                root = f"{letter}:\\{pf_dir}"
                if not os.path.isdir(root):
                    continue
                try:
                    for entry in os.scandir(root):
                        if not entry.is_dir():
                            continue
                        try:
                            for exe in os.scandir(entry.path):
                                base = os.path.splitext(exe.name)[0].lower()
                                if (exe.name.lower().endswith('.exe') and exe.is_file()
                                        and base not in _SKIP_EXE):
                                    apps.append({
                                        "name": entry.name,
                                        "path": exe.path,
                                        "source": "program_files",
                                        "confidence": 0.7,
                                    })
                                    break
                        except (PermissionError, OSError):
                            pass
                except (PermissionError, OSError):
                    pass
        return apps

    @staticmethod
    def _scan_shortcuts() -> list[dict]:
        """Scan Start Menu, Desktop, and Local Programs for .lnk shortcuts."""
        import os, subprocess, json
        apps = []
        folders = [
            os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ.get("USERPROFILE", ""), "Desktop") if os.environ.get("USERPROFILE") else "",
        ]
        # Add user's real desktop (may be relocated to another drive)
        for env_key in ("ONEDRIVE",):
            od = os.environ.get(env_key, "")
            if od:
                od_desktop = os.path.join(od, "Desktop")
                if os.path.isdir(od_desktop) and od_desktop not in folders:
                    folders.append(od_desktop)
        # Read actual Desktop path from registry (handles F:\桌面 etc.)
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
            desktop_reg, _ = winreg.QueryValueEx(key, "Desktop")
            winreg.CloseKey(key)
            desktop_reg = os.path.expandvars(desktop_reg)
            if os.path.isdir(desktop_reg) and desktop_reg not in folders:
                folders.append(desktop_reg)
        except Exception:
            pass
        # Add %LOCALAPPDATA%\Programs (many modern apps install here)
        local_programs = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")
        if os.path.isdir(local_programs) and local_programs not in folders:
            folders.append(local_programs)
        # Add Quick Launch folder
        quick_launch = os.path.join(os.environ.get("APPDATA", ""),
            r"Microsoft\Internet Explorer\Quick Launch")
        if os.path.isdir(quick_launch) and quick_launch not in folders:
            folders.append(quick_launch)
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

        # Resolve all .lnk targets via PowerShell (much faster than per-call COM).
        # Batch the paths so the command line never approaches the Windows
        # 32767-char limit on machines with thousands of shortcuts.
        BATCH_SIZE = 200
        for offset in range(0, len(lnk_paths), BATCH_SIZE):
            batch = lnk_paths[offset:offset + BATCH_SIZE]
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
''' % ';'.join("'%s'" % p.replace("'", "''") for p in batch)

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
            except Exception:
                pass
        return apps

    @staticmethod
    def _scan_drive_roots() -> list[dict]:
        """Scan non-C: drive root directories for common app folders."""
        import os
        apps = []
        _SKIP_EXE = {"uninstall", "uninst", "update", "setup", "crash", "helper",
                      "installer", "repair", "config", "diagnostic"}
        for letter in "DEFGH":
            root = f"{letter}:\\"
            if not os.path.isdir(root):
                continue
            try:
                for entry in os.scandir(root):
                    if not entry.is_dir() or entry.name.startswith(('$', '.', '~')):
                        continue
                    try:
                        for exe in os.scandir(entry.path):
                            base = os.path.splitext(exe.name)[0].lower()
                            if (exe.name.lower().endswith('.exe') and exe.is_file()
                                    and base not in _SKIP_EXE):
                                apps.append({
                                    "name": os.path.splitext(exe.name)[0],
                                    "path": exe.path,
                                    "source": "drive_scan",
                                    "confidence": 0.5,
                                })
                    except (PermissionError, OSError):
                        pass
            except (PermissionError, OSError):
                pass
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

    # ── Linux profile ───────────────────────────────────────────────────────

    def _profile_linux(self, reason: str) -> None:
        """Linux (X11) system profile: XDG folders, .desktop apps, displays."""
        import locale
        import platform
        from datetime import datetime, timezone

        index = self.load_index()
        try:
            language = locale.getlocale()[0] or "unknown"
        except (ValueError, TypeError, AttributeError):
            language = "unknown"

        index["system"] = {
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "language": language,
            "timezone": str(datetime.now(timezone.utc).astimezone().tzinfo),
            "os_version": platform.version(),
            "os_release": platform.release(),
            "session_type": os.environ.get("XDG_SESSION_TYPE", "unknown"),
        }
        index["hardware"] = self._detect_linux_hardware()
        index["displays"] = self._detect_linux_displays()
        index["known_folders"] = self._detect_xdg_folders()
        index["applications"] = self._scan_desktop_files()

    @staticmethod
    def _detect_linux_hardware() -> dict:
        """CPU / memory from /proc; GPU via lspci (best-effort)."""
        import platform
        import subprocess

        result = {"cpu": platform.processor() or "unknown", "cpu_cores": 0,
                  "cpu_threads": 0, "gpus": [], "memory_gb": 0, "storage": []}
        try:
            for line in Path("/proc/cpuinfo").read_text(
                    encoding="utf-8", errors="replace").splitlines():
                if line.startswith("model name"):
                    result["cpu"] = line.split(":", 1)[1].strip()
                elif line.startswith("processor"):
                    result["cpu_threads"] += 1
            result["cpu_cores"] = result["cpu_threads"]  # cores≈threads fallback
        except OSError:
            pass
        try:
            for line in Path("/proc/meminfo").read_text(
                    encoding="utf-8", errors="replace").splitlines():
                if line.startswith("MemTotal"):
                    kb = int(line.split()[1])
                    result["memory_gb"] = round(kb / 1024 / 1024, 1)
                    break
        except (OSError, ValueError):
            pass
        try:
            lspci = subprocess.run(["lspci"], capture_output=True, text=True,
                                   timeout=5)
            for line in lspci.stdout.splitlines():
                if "VGA" in line or "3D controller" in line:
                    name = line.split(":", 2)[-1].strip()
                    result["gpus"].append({"name": name})
        except (OSError, subprocess.TimeoutExpired):
            pass
        return result

    @staticmethod
    def _xdpi_scale() -> float:
        """Xft.dpi / 96 from the X server (1.0 when unavailable)."""
        try:
            from Xlib import display as xdisplay
            d = xdisplay.Display()
            dpi_value = d.get_default(d.screen().root, "Xft", "dpi")
            if dpi_value:
                return max(1.0, float(dpi_value) / 96.0)
        except Exception:
            pass
        return 1.0

    @staticmethod
    def _detect_linux_displays() -> list[dict]:
        """Monitor list via mss + Xft.dpi scale factor.

        Headless sessions return the same 'Undetected' marker as Windows
        (never fake a resolution — it would corrupt coordinate conversion).
        """
        displays = []
        scale = SystemProfiler._xdpi_scale()
        if mss is not None:
            try:
                with mss.mss() as sct:
                    monitors = sct.monitors
                # monitors[0] = virtual screen; [1:] = physical monitors
                for i, mon in enumerate(monitors[1:], start=1):
                    dpi = round(96 * scale)
                    displays.append({
                        "width": mon["width"],
                        "height": mon["height"],
                        "dpi": dpi,
                        "scale_factor": round(scale, 2),
                        "scale_percent": round(scale * 100),
                        "primary": i == 1,
                        "left": mon["left"],
                        "top": mon["top"],
                        "name": f"Display {i}",
                    })
            except Exception:
                displays = []
        if displays:
            return displays
        return [{"width": 0, "height": 0, "dpi": 0, "scale_factor": 0.0,
                 "scale_percent": 0, "primary": True, "left": 0, "top": 0,
                 "name": "Undetected", "detected": False}]

    @staticmethod
    def _detect_xdg_folders() -> dict:
        """XDG user dirs (user-dirs.dirs) with standard fallbacks."""
        folders: dict = {}
        home = Path(os.environ.get("HOME") or Path.home())
        xdg_names = {
            "XDG_DESKTOP_DIR": "桌面",
            "XDG_DOCUMENTS_DIR": "文档",
            "XDG_DOWNLOAD_DIR": "下载",
            "XDG_PICTURES_DIR": "图片",
            "XDG_MUSIC_DIR": "音乐",
            "XDG_VIDEOS_DIR": "视频",
        }
        defaults = {
            "XDG_DESKTOP_DIR": home / "Desktop",
            "XDG_DOCUMENTS_DIR": home / "Documents",
            "XDG_DOWNLOAD_DIR": home / "Downloads",
            "XDG_PICTURES_DIR": home / "Pictures",
            "XDG_MUSIC_DIR": home / "Music",
            "XDG_VIDEOS_DIR": home / "Videos",
        }
        parsed: dict = {}
        user_dirs = home / ".config" / "user-dirs.dirs"
        try:
            for line in user_dirs.read_text(encoding="utf-8",
                                            errors="replace").splitlines():
                line = line.strip()
                if line.startswith("XDG_") and "=" in line:
                    key, value = line.split("=", 1)
                    value = value.strip().strip('"')
                    if value.startswith("$HOME/"):
                        value = str(home / value[len("$HOME/"):])
                    parsed[key] = value
        except OSError:
            pass
        for env_name, label in xdg_names.items():
            value = parsed.get(env_name) or os.environ.get(env_name)
            if not value:
                value = str(defaults[env_name])
            folders[label] = {"path": value}
        folders["主目录"] = {"path": str(home)}
        return folders

    @staticmethod
    def _scan_desktop_files() -> list[dict]:
        """Scan .desktop files from the standard application directories."""
        import configparser

        roots = [
            Path("/usr/share/applications"),
            Path("/usr/local/share/applications"),
            Path(os.environ.get("HOME") or Path.home())
            / ".local" / "share" / "applications",
        ]
        apps: list[dict] = []
        seen: set = set()
        for root in roots:
            if not root.is_dir():
                continue
            try:
                files = sorted(root.glob("*.desktop"))
            except OSError:
                continue
            for desktop in files:
                try:
                    parser = configparser.ConfigParser()
                    parser.read(desktop, encoding="utf-8")
                    if not parser.has_section("Desktop Entry"):
                        continue
                    entry = parser["Desktop Entry"]
                    if entry.get("Type", "") != "Application":
                        continue
                    if entry.get("NoDisplay", "false").lower() == "true":
                        continue
                    name = entry.get("Name", desktop.stem)
                    command = entry.get("Exec", "")
                    try_exec = entry.get("TryExec", "")
                    if try_exec and which(try_exec) is None:
                        continue  # app not actually installed
                    exec_word = (command.split(" ", 1)[0].strip()
                                 if command else "")
                    if exec_word.startswith("%"):
                        exec_word = ""
                    path = which(exec_word) if exec_word else ""
                    dedupe = (name, command)
                    if dedupe in seen:
                        continue
                    seen.add(dedupe)
                    apps.append({
                        "name": name,
                        "path": path,
                        "command": command,
                        "icon": entry.get("Icon", ""),
                        "source": "desktop",
                        "confidence": 0.9,
                    })
                except Exception:
                    continue
        return apps

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

        # Also write to Reasonix global memory (AI retrievable)
        rm_dir = reasonix_global_memory_dir()
        if rm_dir:
            mem_md = self._render_reasonix_memory(index)
            _atomic_write(rm_dir / "computer-use-system-profile.md", mem_md)

    def _render_reasonix_memory(self, index: Dict[str, Any]) -> str:
        """Render a compact Reasonix memory file for the AI."""
        hw = index.get("hardware", {})
        sys_info = index.get("system", {})
        displays = index.get("displays", [])
        apps = index.get("applications", [])
        folders = index.get("known_folders", {})
        cats = index.get("app_categories", {})

        lines = [
            "# Computer Use 系统画像",
            "",
            f"> 更新：{index.get('updated_at', '?')}",
            f"> 插件版本：{__version__}",
            "",
            "## 硬件",
            f"- CPU: {hw.get('cpu', '?')} ({hw.get('cpu_cores', '?')}C/{hw.get('cpu_threads', '?')}T)",
            f"- GPU: " + ", ".join(g.get('name', g.get('name', '?')) for g in hw.get('gpus', [])),
            f"- RAM: {hw.get('memory_gb', '?')}GB",
            f"- 存储: " + ", ".join(d.get('drive', '?') for d in hw.get('storage', [])),
            "",
            "## 显示器",
        ]
        for d in displays:
            lines.append(f"- {d.get('width')}×{d.get('height')} @ {d.get('dpi')}DPI (scale_factor={d.get('scale_factor', 1.0)})")
        lines.extend([
            "",
            "## 常用目录",
        ])
        for name, data in folders.items():
            path = data.get("path") if isinstance(data, dict) else data
            lines.append(f"- {name}: `{path}`")
        lines.extend([
            "",
            "## 应用索引",
            f"共 {len(apps)} 个应用",
            f"注册表: {cats.get('registry', 0)}, 开始菜单: {cats.get('start_menu', 0)}, UWP: {cats.get('uwp', 0)}",
            "",
            "### 常用应用",
        ])
        for app in apps[:30]:
            lines.append(f"- `{app.get('name', '?')}` → `{app.get('path', '?')}`")
        if len(apps) > 30:
            lines.append(f"> 还有 {len(apps) - 30} 个应用，用 computer_app(launch) 自动解析。")
        return "\n".join(lines)

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
            lines.append(f"- 注册表 Uninstall：{cats.get('uninstall', 0)}")
            lines.append(f"- 开始菜单快捷方式：{cats.get('start_menu', 0)}")
            lines.append(f"- UWP/Store 应用：{cats.get('uwp', 0)}")
            lines.append(f"- 当前运行中：{cats.get('running', 0)}")
            lines.append(f"- 盘根目录扫描：{cats.get('drive_scan', 0)}")
            lines.append(f"- Program Files：{cats.get('program_files', 0)}")
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
