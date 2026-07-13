"""Fast Windows environment and application index for Reasonix."""

from __future__ import annotations

import ctypes
import datetime as dt
import hashlib
import json
import locale
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import winreg
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path
from typing import Any

from .system_profile import read_index, write_profile_and_index
from .windows import DPI_AWARENESS, list_windows, virtual_screen, window_dpi


SCHEMA_VERSION = 2
_LOCK = threading.RLock()
_ENRICHING = False
_WATCHER_STARTED = False
_BAD_EXECUTABLES = ("unins", "uninstall", "setup", "installer", "update", "crash", "helper", "service")
_NON_APP_NAMES = (" update", "updater", "webview", " runtime", "redistributable")
APP_ALIASES = {
    "记事本": "notepad", "文本编辑器": "notepad",
    "计算器": "calculator", "画图": "paint", "截图工具": "snipping tool",
    "文件资源管理器": "explorer", "资源管理器": "explorer",
    "系统设置": "settings", "windows 设置": "settings",
    "任务管理器": "task manager", "终端": "windows terminal",
}
SEARCH_SYNONYMS = {
    "calculator": ("calculator", "计算器", "windows calculator"),
}


def _is_non_app_name(name: str) -> bool:
    lowered_name = name.casefold()
    return any(token in lowered_name for token in _NON_APP_NAMES)

FOLDER_VALUES = {
    "桌面": "Desktop",
    "文档": "Personal",
    "下载": "{374DE290-123F-4565-9164-39C4925E467B}",
    "图片": "My Pictures",
    "音乐": "My Music",
    "视频": "My Video",
    "开始菜单": "Start Menu",
    "程序菜单": "Programs",
}


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _known_folders() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders", "user-shell-folders"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders", "shell-folders"),
    ]
    for hive, key_name, source in roots:
        try:
            key = winreg.OpenKey(hive, key_name)
        except OSError:
            continue
        try:
            for label, value_name in FOLDER_VALUES.items():
                if label in result:
                    continue
                try:
                    value = os.path.expandvars(str(winreg.QueryValueEx(key, value_name)[0]))
                    result[label] = {"path": os.path.normpath(value), "source": source, "confidence": 1.0}
                except OSError:
                    pass
        finally:
            winreg.CloseKey(key)
    result["用户主目录"] = {
        "path": os.path.normpath(os.environ.get("USERPROFILE", str(Path.home()))),
        "source": "environment", "confidence": 0.9,
    }
    return result


def _hardware() -> dict[str, Any]:
    cpu = platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown")
    memory_gb: float | str = "unknown"
    try:
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD),
                        ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                        ("page_total", ctypes.c_ulonglong), ("page_available", ctypes.c_ulonglong),
                        ("virtual_total", ctypes.c_ulonglong), ("virtual_available", ctypes.c_ulonglong),
                        ("extended", ctypes.c_ulonglong)]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            memory_gb = round(status.total / (1024 ** 3), 1)
    except (AttributeError, OSError):
        pass
    gpu = "unknown"
    try:
        output = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join '; '"],
            capture_output=True, timeout=3, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
        gpu = output or gpu
    except (OSError, subprocess.SubprocessError):
        pass
    return {"cpu": cpu, "gpu": gpu, "memory_gb": memory_gb}


def _displays() -> list[dict[str, Any]]:
    displays: list[dict[str, Any]] = []
    try:
        monitor_dpi = ctypes.windll.shcore.GetDpiForMonitor
    except AttributeError:
        monitor_dpi = None

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC,
                       ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def callback(handle, _dc, rect_ptr, _data):
        rect = rect_ptr.contents
        dpi_x = wintypes.UINT(96)
        dpi_y = wintypes.UINT(96)
        if monitor_dpi:
            try:
                monitor_dpi(handle, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
            except OSError:
                pass
        displays.append({
            "name": f"显示器 {len(displays) + 1}", "left": rect.left, "top": rect.top,
            "width": rect.right - rect.left, "height": rect.bottom - rect.top,
            "dpi": int(dpi_x.value), "scale_percent": round(dpi_x.value / 96 * 100),
            "primary": rect.left == 0 and rect.top == 0,
        })
        return True

    try:
        ctypes.windll.user32.EnumDisplayMonitors(0, 0, callback, 0)
    except (AttributeError, OSError):
        pass
    if not displays:
        screen = virtual_screen()
        displays.append({"name": "虚拟屏幕", **screen, "dpi": 96, "scale_percent": 100, "primary": True})
    return displays


def _clean_executable(value: str) -> str:
    value = os.path.expandvars(str(value or "")).strip().strip('"')
    match = re.match(r'^(.*?\.exe)(?:[," ]|$)', value, re.IGNORECASE)
    return os.path.normpath(match.group(1)) if match else ""


def _launchable_executable(value: str) -> bool:
    stem = Path(value).stem.casefold()
    return bool(value) and os.path.isfile(value) and not any(token in stem for token in _BAD_EXECUTABLES)


def _best_executable(directory: str, name: str) -> str:
    path = Path(directory)
    if not path.is_dir():
        return ""
    needle = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", name.casefold())
    candidates = []
    try:
        for item in path.glob("*.exe"):
            stem = item.stem.casefold()
            if any(token in stem for token in _BAD_EXECUTABLES):
                continue
            normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", stem)
            score = (normalized != needle, needle not in normalized and normalized not in needle,
                     "app" not in stem, len(stem))
            candidates.append((score, str(item)))
    except OSError:
        return ""
    return min(candidates)[1] if candidates else ""


def _app_id(name: str, target: str) -> str:
    seed = f"{name.casefold()}|{target.casefold()}".encode("utf-8", "replace")
    return hashlib.sha1(seed).hexdigest()[:12]


def _entry(name: str, target: str, source: str, confidence: float,
           version: str = "", publisher: str = "", args: str = "") -> dict[str, Any]:
    target = os.path.normpath(target) if target else ""
    return {
        "id": _app_id(name, target), "name": name.strip(), "version": version.strip(),
        "publisher": publisher.strip(), "path": target, "launch_target": target,
        "launch_args": args, "source": source, "confidence": confidence,
        "first_seen": _now(), "last_verified": _now(),
    }


def _scan_app_paths() -> list[dict[str, Any]]:
    apps = []
    roots = (
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
    )
    for hive, root in roots:
        try:
            key = winreg.OpenKey(hive, root)
        except OSError:
            continue
        try:
            for index in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub_name = winreg.EnumKey(key, index)
                    sub = winreg.OpenKey(key, sub_name)
                    target = _clean_executable(winreg.QueryValue(sub, ""))
                    winreg.CloseKey(sub)
                    if target and os.path.isfile(target):
                        apps.append(_entry(Path(sub_name).stem, target, "app-paths", 1.0))
                except OSError:
                    continue
        finally:
            winreg.CloseKey(key)
    return apps


def _scan_uninstall() -> list[dict[str, Any]]:
    apps = []
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, root in roots:
        try:
            key = winreg.OpenKey(hive, root)
        except OSError:
            continue
        try:
            for index in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub = winreg.OpenKey(key, winreg.EnumKey(key, index))
                    values = {}
                    for field in ("DisplayName", "DisplayVersion", "Publisher", "DisplayIcon", "InstallLocation"):
                        try:
                            values[field] = str(winreg.QueryValueEx(sub, field)[0])
                        except OSError:
                            values[field] = ""
                    winreg.CloseKey(sub)
                    name = values["DisplayName"].strip()
                    if not name:
                        continue
                    if _is_non_app_name(name):
                        continue
                    target = _clean_executable(values["DisplayIcon"])
                    if not _launchable_executable(target):
                        target = _best_executable(values["InstallLocation"], name)
                    apps.append(_entry(name, target, "uninstall-registry", 0.8,
                                       values["DisplayVersion"], values["Publisher"]))
                except OSError:
                    continue
        finally:
            winreg.CloseKey(key)
    return apps


def _scan_shortcuts() -> list[dict[str, Any]]:
    command = (
        "$ErrorActionPreference='SilentlyContinue';$OutputEncoding=[Text.Encoding]::UTF8;"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;$w=New-Object -ComObject WScript.Shell;"
        "$d=@([Environment]::GetFolderPath('Programs'),[Environment]::GetFolderPath('CommonPrograms'),"
        "[Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('CommonDesktopDirectory'));"
        "foreach($p in $d){if(Test-Path $p){Get-ChildItem -LiteralPath $p -Recurse -Filter *.lnk|%{"
        "$s=$w.CreateShortcut($_.FullName);if($s.TargetPath){[Console]::WriteLine("
        "($_.BaseName+[char]9+$s.TargetPath+[char]9+$s.Arguments))}}}}"
    )
    try:
        output = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, timeout=4, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    apps = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) >= 2:
            target = os.path.expandvars(parts[1].strip())
            if target.lower().endswith(".exe") and os.path.isfile(target):
                apps.append(_entry(parts[0], target, "shortcut", 1.0,
                                   args=parts[2].strip() if len(parts) > 2 else ""))
    return apps


def _running_apps() -> list[dict[str, Any]]:
    apps = []
    seen = set()
    for window in list_windows():
        target = window.process_path
        if not target or target.casefold() in seen:
            continue
        seen.add(target.casefold())
        apps.append(_entry(Path(target).stem, target, "running-window", 0.95))
    return apps


def _scan_start_apps() -> list[dict[str, Any]]:
    command = ("$OutputEncoding=[Text.Encoding]::UTF8;"
               "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
               "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
    try:
        output = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, timeout=10, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
        data = json.loads(output) if output else []
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = [data]
    return [_entry(str(item.get("Name", "")), f"shell:AppsFolder\\{item.get('AppID', '')}",
                   "start-apps", 0.9) for item in data if item.get("Name") and item.get("AppID")]


def _scan_portable_roots(max_seconds: float = 20.0) -> list[dict[str, Any]]:
    """Shallow background scan of non-system drives, never a full disk walk."""
    started = time.monotonic()
    apps = []
    ignored = {"$recycle.bin", "system volume information", "windowsapps", "users", "recovery",
               "desktop", "documents", "downloads", "桌面", "文档", "下载", "图片", "音乐", "视频"}
    system_drive = os.environ.get("SystemDrive", "C:").rstrip("\\").casefold()
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:\\")
        if letter.casefold() == system_drive[:1] or not root.is_dir():
            continue
        try:
            top = [item for item in root.iterdir() if item.is_dir() and item.name.casefold() not in ignored]
        except OSError:
            continue
        for folder in top:
            if time.monotonic() - started > max_seconds:
                return apps
            directories = [folder]
            try:
                directories.extend(item for item in folder.iterdir() if item.is_dir())
            except OSError:
                pass
            for directory in directories[:80]:
                try:
                    for executable in directory.glob("*.exe"):
                        if not _launchable_executable(str(executable)):
                            continue
                        name = folder.name if directory == folder else directory.name
                        target = str(executable)
                        normalized_name = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", name.casefold())
                        normalized_stem = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", executable.stem.casefold())
                        confidence = 0.85 if normalized_name in normalized_stem or normalized_stem in normalized_name else 0.55
                        apps.append(_entry(name, target, "portable-shallow", confidence))
                except OSError:
                    continue
    return apps


def _file_hash(path: str) -> str:
    if not _launchable_executable(path):
        return ""
    try:
        if os.path.getsize(path) > 200 * 1024 * 1024:
            return ""
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _default_apps() -> dict[str, str]:
    defaults = {"文件管理器": os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")}
    terminal = shutil.which("wt.exe") or os.environ.get("COMSPEC", "")
    if terminal:
        defaults["终端"] = terminal
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice")
        prog_id = str(winreg.QueryValueEx(key, "ProgId")[0])
        winreg.CloseKey(key)
        command_key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r"\shell\open\command")
        browser = _clean_executable(str(winreg.QueryValue(command_key, "")))
        winreg.CloseKey(command_key)
        defaults["浏览器"] = browser or prog_id
    except OSError:
        pass
    return defaults


def _merge_apps(apps: list[dict[str, Any]], previous: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in (previous or []) + apps:
        path = str(item.get("path", ""))
        if path.casefold().endswith(".exe") and not _launchable_executable(path):
            continue
        key = (str(item.get("name", "")).casefold(), str(item.get("path", "")).casefold())
        if not key[0]:
            continue
        current = merged.get(key)
        if current is None or float(item.get("confidence", 0)) >= float(current.get("confidence", 0)):
            if current and current.get("first_seen"):
                item["first_seen"] = current["first_seen"]
                for field in ("version", "publisher", "launch_args"):
                    if not item.get(field) and current.get(field):
                        item[field] = current[field]
                if not item.get("sha256") and item.get("version") == current.get("version"):
                    item["sha256"] = current.get("sha256", "")
            merged[key] = item
        elif current:
            for field in ("version", "publisher", "sha256", "launch_args"):
                if not current.get(field) and item.get(field):
                    current[field] = item[field]
    return sorted(merged.values(), key=lambda item: (-float(item.get("confidence", 0)), item["name"].casefold()))


def build_index(reason: str = "first-run", enrich: bool = False) -> dict[str, Any]:
    with _LOCK:
        previous = read_index() or {}
        # The two PowerShell-backed probes (GPU and shortcuts) are the slowest.
        # Run independent probes together so the first usable index stays below
        # the SessionStart latency target.
        with ThreadPoolExecutor(max_workers=7, thread_name_prefix="reasonix-index") as executor:
            hardware_future = executor.submit(_hardware)
            displays_future = executor.submit(_displays)
            folders_future = executor.submit(_known_folders)
            app_paths_future = executor.submit(_scan_app_paths)
            shortcuts_future = executor.submit(_scan_shortcuts)
            uninstall_future = executor.submit(_scan_uninstall)
            running_future = executor.submit(_running_apps)
            defaults_future = executor.submit(_default_apps)
            hardware = hardware_future.result()
            displays = displays_future.result()
            folders = folders_future.result()
            applications = (app_paths_future.result() + shortcuts_future.result()
                            + uninstall_future.result() + running_future.result())
            defaults = defaults_future.result()
        previous_apps = [item for item in previous.get("applications", [])
                         if str(item.get("path", "")).casefold().startswith("shell:appsfolder")
                         or _launchable_executable(str(item.get("path", "")))]
        index = {
            "schema_version": SCHEMA_VERSION, "updated_at": _now(), "reason": reason,
            "quick_scan_complete": True, "enrichment_complete": bool(enrich),
            "system": {
                "platform": platform.platform(), "architecture": platform.machine(),
                "language": locale.getlocale()[0] or "unknown",
                "timezone": dt.datetime.now().astimezone().tzname(), "dpi_awareness": DPI_AWARENESS,
                "virtual_screen": virtual_screen(),
            },
            "hardware": hardware, "displays": displays, "known_folders": folders,
            "default_apps": defaults or previous.get("default_apps", {}),
            "applications": _merge_apps(applications, previous_apps),
        }
        write_profile_and_index(index)
        return index


def ensure_index(reason: str = "first-run", refresh: bool = False) -> dict[str, Any]:
    current = read_index()
    if refresh or not current or current.get("schema_version") != SCHEMA_VERSION:
        return build_index(reason)
    return current


def enrich_index(reason: str = "background-enrichment") -> dict[str, Any]:
    """Add portable and StartApps entries without delaying SessionStart."""
    portable = _scan_portable_roots()
    start_apps = _scan_start_apps()
    with _LOCK:
        current = ensure_index()
        applications = _merge_apps(portable + start_apps, current.get("applications"))
        hash_targets = [item for item in applications if item.get("path") and not str(item["path"]).startswith("shell:")
                        and not item.get("sha256")][:120]
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="reasonix-hash") as executor:
            hashes = list(executor.map(lambda item: _file_hash(str(item.get("path", ""))), hash_targets))
        for item, digest in zip(hash_targets, hashes):
            if digest:
                item["sha256"] = digest
        current.update({"updated_at": _now(), "reason": reason, "applications": applications,
                        "enrichment_complete": True})
        write_profile_and_index(current)
        return current


def start_background_enrichment() -> bool:
    global _ENRICHING
    with _LOCK:
        current = read_index()
        if _ENRICHING or (current and current.get("enrichment_complete")):
            return False
        _ENRICHING = True

    def run() -> None:
        global _ENRICHING
        try:
            enrich_index()
        finally:
            with _LOCK:
                _ENRICHING = False

    threading.Thread(target=run, name="reasonix-system-enrichment", daemon=True).start()
    return True


def _install_signature() -> tuple[Any, ...]:
    values: list[Any] = []
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    )
    for hive, root in roots:
        try:
            key = winreg.OpenKey(hive, root)
            info = winreg.QueryInfoKey(key)
            winreg.CloseKey(key)
            values.extend((info[0], info[2]))
        except OSError:
            values.extend((0, 0))
    for folder in _known_folders().values():
        path = str(folder.get("path", ""))
        if path and ("Start Menu" in path or Path(path).name.casefold() in ("桌面", "desktop")):
            try:
                values.append(os.path.getmtime(path))
            except OSError:
                values.append(0)
    return tuple(values)


def start_change_watcher(interval_seconds: float = 60.0) -> bool:
    global _WATCHER_STARTED
    with _LOCK:
        if _WATCHER_STARTED:
            return False
        _WATCHER_STARTED = True

    def watch() -> None:
        previous = _install_signature()
        while True:
            time.sleep(max(interval_seconds, 10.0))
            current = _install_signature()
            if current == previous:
                continue
            previous = current
            try:
                build_index("software-change-detected")
                start_background_enrichment()
            except Exception:
                continue

    threading.Thread(target=watch, name="reasonix-software-watcher", daemon=True).start()
    return True


def search_apps(query: str, limit: int = 10, refresh_on_miss: bool = True) -> list[dict[str, Any]]:
    index = ensure_index()
    requested = query.strip().casefold()
    needle = APP_ALIASES.get(requested, requested)
    query_terms = tuple(dict.fromkeys((requested, needle, *SEARCH_SYNONYMS.get(needle, ()))))

    def rank(item: dict[str, Any]) -> tuple:
        name = str(item.get("name", "")).casefold()
        path = str(item.get("path") or "")
        stem = Path(path).stem.casefold()
        exact = name in query_terms or stem in query_terms
        gui_name = any(name in (f"{term} app", f"{term} desktop") for term in query_terms)
        starts = any(name.startswith(term) or stem.startswith(term) for term in query_terms if term)
        contains = any(term in name or term in stem for term in query_terms if term)
        return (not exact, not gui_name, not starts, not contains,
                -float(item.get("confidence", 0)), path.casefold().startswith("shell:appsfolder"), len(name))

    def matches_query(item: dict[str, Any]) -> bool:
        name = str(item.get("name", "")).casefold()
        stem = Path(str(item.get("path") or "")).stem.casefold()
        return any(value and (value in name or value in stem) for value in query_terms)

    def has_strong_match(items: list[dict[str, Any]]) -> bool:
        return any(str(item.get("name", "")).casefold() in query_terms
                   or Path(str(item.get("path") or "")).stem.casefold() in query_terms
                   for item in items)

    matches = [item for item in index.get("applications", []) if matches_query(item)]
    if refresh_on_miss and (not matches or not has_strong_match(matches)):
        # UWP/MSIX system apps such as Calculator primarily live in StartApps.
        # Query that focused source before rebuilding the wider machine index.
        start_apps = _scan_start_apps()
        if start_apps:
            index["applications"] = _merge_apps(start_apps, index.get("applications", []))
            index["updated_at"] = _now()
            index["reason"] = f"start-apps-miss:{query}"
            write_profile_and_index(index)
            matches = [item for item in index["applications"] if matches_query(item)]
        if not matches:
            index = build_index(f"application-miss:{query}")
            matches = [item for item in index.get("applications", []) if matches_query(item)]
    selected = sorted(matches, key=rank)[:max(1, min(int(limit), 10))]
    if any(item.get("path") and not item.get("sha256") and _launchable_executable(str(item["path"]))
           for item in selected):
        with _LOCK:
            changed = False
            for item in selected:
                if item.get("sha256"):
                    continue
                digest = _file_hash(str(item.get("path", "")))
                if digest:
                    item["sha256"] = digest
                    changed = True
            if changed:
                index["updated_at"] = _now()
                index["reason"] = f"application-hash:{query}"
                write_profile_and_index(index)
    return selected


def find_app(app_id: str) -> dict[str, Any] | None:
    index = ensure_index()
    item = next((candidate for candidate in index.get("applications", []) if candidate.get("id") == app_id), None)
    if item and not item.get("sha256"):
        digest = _file_hash(str(item.get("path", "")))
        if digest:
            item["sha256"] = digest
            index["updated_at"] = _now()
            index["reason"] = f"application-hash:{item.get('name', app_id)}"
            write_profile_and_index(index)
    return item


def query_profile(target: str = "") -> dict[str, Any]:
    index = ensure_index()
    if not target:
        return {key: index.get(key) for key in ("updated_at", "system", "hardware", "displays", "known_folders")}
    apps = search_apps(target, refresh_on_miss=False)
    folders = {name: value for name, value in index.get("known_folders", {}).items()
               if target.casefold() in name.casefold() or target.casefold() in str(value).casefold()}
    return {"query": target, "applications": apps, "known_folders": folders}
