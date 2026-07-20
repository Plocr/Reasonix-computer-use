"""Cross-platform process management backend.

Windows: uses WMI + Job Objects (via windows.py).
Other: uses psutil.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any

from .platform_backend import IS_WINDOWS


def kill_process(pid: int) -> bool:
    """Kill a process by PID. Returns True if successful."""
    try:
        import psutil
        process = psutil.Process(pid)
        process.kill()
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, ImportError):
        if IS_WINDOWS:
            return _kill_process_windows(pid)
        return False


def _kill_process_windows(pid: int) -> bool:
    """Windows-specific process kill via kernel32."""
    try:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)
        if not handle:
            return False
        try:
            result = ctypes.windll.kernel32.TerminateProcess(handle, 0)
            return bool(result)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except (AttributeError, OSError):
        return False


def launch_process(command: str, args: str = "", workdir: str = "") -> tuple[int, str]:
    """Launch a process detached. Returns (pid, method)."""
    try:
        import psutil
        creationflags = 0
        if IS_WINDOWS:
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        process = psutil.Popen(
            [command] + (args.split() if args else []),
            cwd=workdir or None,
            creationflags=creationflags if IS_WINDOWS else 0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        return process.pid, "psutil"
    except (ImportError, Exception) as e:
        if IS_WINDOWS:
            return _launch_process_windows(command, args, workdir)
        raise


def _launch_process_windows(command: str, args: str, workdir: str) -> tuple[int, str]:
    """Windows-specific process launch via WMI."""
    try:
        import subprocess
        cmd = [command] + (args.split() if args else [])
        process = subprocess.Popen(
            cmd,
            cwd=workdir or None,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        return process.pid, "subprocess"
    except Exception:
        return 0, "failed"


def list_running_processes() -> list[dict[str, Any]]:
    """List running processes with basic info."""
    try:
        import psutil
        results = []
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                info = proc.info
                results.append({
                    "pid": info.get("pid", 0),
                    "name": info.get("name", ""),
                    "path": info.get("exe", "") or "",
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return results
    except ImportError:
        if IS_WINDOWS:
            return _list_processes_windows()
        return []


def _list_processes_windows() -> list[dict[str, Any]]:
    """Windows-specific process listing via tasklist."""
    try:
        import subprocess
        output = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        if output.returncode != 0:
            return []
        results = []
        for line in output.stdout.splitlines():
            parts = line.strip('"').split('","')
            if len(parts) >= 2:
                results.append({"name": parts[0], "pid": int(parts[1]), "path": ""})
        return results
    except Exception:
        return []


def get_system_info() -> dict[str, Any]:
    """Get system hardware info."""
    info: dict[str, Any] = {"cpu": "unknown", "memory_gb": "unknown", "gpu": "unknown"}
    try:
        import psutil
        info["memory_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except ImportError:
        pass

    if IS_WINDOWS:
        try:
            import subprocess
            output = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "(Get-CimInstance Win32_Processor).Name"],
                capture_output=True, timeout=5, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if output.stdout.strip():
                info["cpu"] = output.stdout.strip()
        except Exception:
            pass
    return info


def get_display_info() -> list[dict[str, Any]]:
    """Get display/monitor info."""
    if IS_WINDOWS:
        from . import windows
        return windows._displays()
    # Cross-platform: use mss for monitor info
    try:
        import mss
        with mss.mss() as screen_capture:
            monitors = []
            for index, monitor in enumerate(screen_capture.monitors):
                monitors.append({
                    "name": f"显示器 {index}",
                    "left": monitor["left"],
                    "top": monitor["top"],
                    "width": monitor["width"],
                    "height": monitor["height"],
                    "dpi": 96,
                    "scale_percent": 100,
                    "primary": index == 0,
                })
            return monitors
    except ImportError:
        return []


def get_known_folders() -> dict[str, dict[str, Any]]:
    """Get known system folders (Desktop, Documents, etc.)."""
    import pathlib
    home = pathlib.Path.home()
    folders = {
        "用户主目录": {"path": str(home), "confidence": 1.0},
    }

    # Cross-platform standard folders
    if (home / "Desktop").exists():
        folders["桌面"] = {"path": str(home / "Desktop"), "confidence": 1.0}
    if (home / "Documents").exists():
        folders["文档"] = {"path": str(home / "Documents"), "confidence": 1.0}
    if (home / "Downloads").exists():
        folders["下载"] = {"path": str(home / "Downloads"), "confidence": 1.0}

    if IS_WINDOWS:
        # Windows Registry overrides
        try:
            import winreg
            registry_folders = {
                "桌面": "Desktop",
                "文档": "Personal",
                "下载": "{374DE290-123F-4565-9164-39C4925E467B}",
            }
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
            for label, value_name in registry_folders.items():
                if label in folders:
                    continue
                try:
                    value = os.path.expandvars(str(winreg.QueryValueEx(key, value_name)[0]))
                    folders[label] = {"path": os.path.normpath(value), "confidence": 1.0}
                except OSError:
                    pass
            winreg.CloseKey(key)
        except Exception:
            pass

    return folders
