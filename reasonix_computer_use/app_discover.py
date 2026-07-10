"""Computer use tools - Application discover and launch module.

新逻辑：
1. 首次执行（或缓存不存在）：执行两个 PowerShell 命令，结果输出到 memory/applist.md
   - 命令1：注册表扫描（3 个路径）
   - 命令2：Get-AppxPackage（Store 应用）
2. 后续执行（缓存存在）：在 applist.md 中搜索
3. 如果搜索不到：重新执行两个命令，覆盖旧 applist.md
4. 如果仍然找不到：检测绿色软件（桌面快捷方式、下载目录、常见绿色软件目录）
"""

import json
import os
import subprocess
from pathlib import Path
from reasonix_computer_use.mcp_server import register_tool
from reasonix_computer_use.utils import parse_result

CACHE_FILENAME = "applist.md"

# PowerShell 命令：使用 ConvertTo-Csv 替代 Format-Table 避免编码问题
_REGISTRY_COMMAND = r'''
Get-ChildItem -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall" -ErrorAction SilentlyContinue |
    Get-ItemProperty |
    Where-Object { $_.DisplayName } |
    Select-Object DisplayName, DisplayVersion, InstallLocation |
    Sort-Object DisplayName |
    ConvertTo-Csv -NoTypeInformation -Delimiter "`t"
'''

_APPX_COMMAND = '''
Get-AppxPackage | Select PackageFullName, InstallLocation | Sort PackageFullName | ConvertTo-Csv -NoTypeInformation -Delimiter "`t"
'''


def _get_memory_dir():
    """Get the path to the memory directory (inside the plugin package)."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "memory")


def _get_cache_path():
    """Get the full path to the cache file."""
    return os.path.join(_get_memory_dir(), CACHE_FILENAME)


def _scan_registry_with_powershell():
    """Execute both PowerShell commands and return combined results."""
    results = []

    # Command 1: Registry scan
    proc1 = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _REGISTRY_COMMAND],
        capture_output=True, text=True, timeout=60, encoding='utf-8', errors='replace'
    )
    if proc1.returncode == 0 and proc1.stdout.strip():
        results.append(proc1.stdout)
    else:
        results.append(f"# Registry scan failed: {proc1.stderr[:200]}")

    # Command 2: APPx packages
    proc2 = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _APPX_COMMAND],
        capture_output=True, text=True, timeout=60, encoding='utf-8', errors='replace'
    )
    if proc2.returncode == 0 and proc2.stdout.strip():
        results.append(proc2.stdout)
    else:
        results.append(f"# APPx scan failed: {proc2.stderr[:200]}")

    return "\n".join(results)


def _write_cache(content):
    """Write scan results to cache file (overwrite mode)."""
    cache_path = Path(_get_cache_path())
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = subprocess.getoutput('powershell -Command "Get-Date -Format yyyy-MM-dd HH:mm:ss"')
    header = f"# Installed Applications List\n\n> Auto-generated: {timestamp}\n> Sources: Registry + Microsoft Store (Get-AppxPackage)\n\n"

    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(content)

    return str(cache_path)


def _read_cache():
    """Read cached app list from file."""
    cache_path = Path(_get_cache_path())
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _search_in_cache(cache_content, search_term):
    """Search for an app in cached content (CSV format, tab-delimited)."""
    if not cache_content:
        return []

    results = []
    search_lower = search_term.lower()

    for line in cache_content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith(">"):
            continue
        # Skip CSV header line
        if stripped.startswith('"DisplayName"') or stripped.startswith('"PackageFullName"'):
            continue

        if search_lower in stripped.lower():
            results.append(stripped)

    return results


def _resolve_lnk_target(lnk_path):
    """Resolve a .lnk shortcut file to its target path (pure Python, no win32com)."""
    try:
        import struct
        with open(lnk_path, 'rb') as f:
            header = f.read(76)
            if header[:4] != b'\x4c\x00\x00\x00':
                return None
            link_flags = struct.unpack('<I', header[20:24])[0]
            if not (link_flags & 0x01):
                return None
            item_id_list_size = struct.unpack('<H', header[76:78])[0]
            link_info_offset = 78 + item_id_list_size
            if link_info_offset >= os.path.getsize(lnk_path):
                return None
            f.seek(link_info_offset)
            link_info_header = f.read(28)
            if len(link_info_header) < 28:
                return None
            flags = struct.unpack('<I', link_info_header[8:12])[0]
            is_unicode = bool(flags & 0x02)
            local_path_offset = struct.unpack('<I', link_info_header[16:20])[0]
            f.seek(link_info_offset + local_path_offset)
            if is_unicode:
                target = []
                while True:
                    char = f.read(2)
                    if char == b'\x00\x00':
                        break
                    target.append(char.decode('utf-16-le'))
                return ''.join(target)
            else:
                target = []
                while True:
                    char = f.read(1)
                    if char == b'\x00':
                        break
                    target.append(char.decode('cp1252', errors='replace'))
                return ''.join(target)
    except:
        return None


def _detect_portable_software(app_name):
    """Detect green/portable software by checking desktop shortcuts and common directories."""
    search_lower = app_name.lower()
    findings = []

    # 1. Check desktop shortcuts (.lnk files)
    desktop_dir = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
    if os.path.isdir(desktop_dir):
        try:
            for item in os.listdir(desktop_dir):
                item_lower = item.lower()
                if search_lower in item_lower and item_lower.endswith(".lnk"):
                    lnk_path = os.path.join(desktop_dir, item)
                    target = _resolve_lnk_target(lnk_path)
                    if target and target.endswith(".exe") and os.path.exists(target):
                        findings.append({
                            "name": os.path.splitext(item)[0],
                            "path": target,
                            "type": "green_software_lnk",
                            "location": desktop_dir,
                            "shortcut": lnk_path
                        })
        except (PermissionError, OSError):
            pass

    # 2. Check common directories for green software
    common_dirs = [
        os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
        os.path.join(os.environ.get("USERPROFILE", ""), "Downloads"),
        os.environ.get("ProgramFiles", ""),
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("APPDATA", ""),
        os.environ.get("ProgramFiles(x86)", ""),
        "C:\\", "D:\\", "E:\\",
    ]

    for base_dir in common_dirs:
        if not base_dir or not os.path.isdir(base_dir):
            continue
        try:
            for item in os.listdir(base_dir):
                item_path = os.path.join(base_dir, item)
                if search_lower in item.lower() and os.path.isdir(item_path):
                    for f in os.listdir(item_path):
                        if f.lower().endswith(".exe"):
                            findings.append({
                                "name": item,
                                "path": os.path.join(item_path, f),
                                "type": "green_software",
                                "location": item_path
                            })
                            break
                if search_lower in item.lower() and item.lower().endswith(".exe") and os.path.isfile(item_path):
                    findings.append({
                        "name": os.path.splitext(item)[0],
                        "path": item_path,
                        "type": "green_software",
                        "location": base_dir
                    })
        except (PermissionError, OSError):
            continue

    return findings


def _extract_install_path(cache_content, app_name):
    """Extract install path for a specific app from cached content (CSV, tab-delimited)."""
    search_lower = app_name.lower()

    for line in cache_content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith(">"):
            continue
        # Skip CSV header line
        if stripped.startswith('"DisplayName"') or stripped.startswith('"PackageFullName"'):
            continue

        if search_lower in stripped.lower():
            # CSV format: "DisplayName"	"DisplayVersion"	"InstallLocation"
            parts = [p.strip().strip('"') for p in stripped.split("\t")]
            if len(parts) >= 3:
                install_path = parts[2].strip()
                if install_path and os.path.isdir(install_path):
                    return install_path

    return None


@register_tool(
    name="computer_app_list",
    description="""Get installed applications list.

Logic:
- First run: Execute PowerShell to scan registry + Microsoft Store
- Cache results to memory/applist.md (CSV format)
- Subsequent runs: Read from cache (fast)
- If search miss: Auto-refresh cache
- If still missing: Detect green software

Parameters:
- search: Search by name (optional)
- refresh: Force rescan (default: false)
""",
    schema={
        "type": "object",
        "properties": {
            "search": {"type": "string", "description": "Search by name"},
            "refresh": {"type": "boolean", "default": False, "description": "Force rescan and overwrite cache"}
        }
    }
)
async def computer_app_list(args: dict) -> str:
    """Get installed applications list."""
    search = args.get("search", "").strip()
    force_refresh = args.get("refresh", False)

    cache_content = None

    # Try reading cache first (unless forced refresh)
    if not force_refresh:
        cache_content = _read_cache()

    # No cache or forced refresh: scan with PowerShell
    if cache_content is None:
        scan_result = _scan_registry_with_powershell()
        cache_path = _write_cache(scan_result)
        cache_content = scan_result

    # Search filter
    if search:
        matches = _search_in_cache(cache_content, search)
        # If not found in cache, try refreshing
        if not matches:
            scan_result = _scan_registry_with_powershell()
            cache_path = _write_cache(scan_result)
            matches = _search_in_cache(scan_result, search)
        return parse_result({
            "status": "ok",
            "search": search,
            "matches": matches,
            "cache_file": CACHE_FILENAME
        })

    # Return full cache content
    return parse_result({
        "status": "ok",
        "content": cache_content,
        "cache_file": CACHE_FILENAME
    })


@register_tool(
    name="computer_app_launch",
    description="""Launch an application.

Search logic (in order):
1. If path provided, launch directly
2. If title provided, search in memory/applist.md cache
3. If not found in cache, auto-rescan and update cache
4. If still not found, try green software detection
5. If still not found, return not found

Parameters:
- path: Executable path or .lnk shortcut
- args: Command line arguments
- title: If path is empty, search by name
""",
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Executable path or .lnk shortcut"},
            "args": {"type": "array", "items": {"type": "string"}, "default": [], "description": "Command line arguments"},
            "title": {"type": "string", "description": "If path is empty, search by name"}
        }
    }
)
async def computer_app_launch(args: dict) -> str:
    """Launch an application."""
    path = args.get("path", "")
    app_args = args.get("args", [])
    title = args.get("title", "")

    try:
        # If no path but title provided, search for the app
        if not path and title:
            # Step 1: Search in cache
            cache_content = _read_cache()
            if cache_content:
                install_path = _extract_install_path(cache_content, title)
                if install_path:
                    for f in os.listdir(install_path):
                        if f.lower().endswith(".exe"):
                            path = os.path.join(install_path, f)
                            break

            # Step 2: If not found, rescan and update cache
            if not path:
                scan_result = _scan_registry_with_powershell()
                cache_path = _write_cache(scan_result)
                install_path = _extract_install_path(scan_result, title)
                if install_path:
                    for f in os.listdir(install_path):
                        if f.lower().endswith(".exe"):
                            path = os.path.join(install_path, f)
                            break

            # Step 3: If still not found, try green software detection
            if not path:
                portable_findings = _detect_portable_software(title)
                if portable_findings:
                    best_match = portable_findings[0]
                    path = best_match["path"]
                    return parse_result({
                        "status": "ok",
                        "action": "found_green_software",
                        "name": best_match["name"],
                        "path": path,
                        "type": best_match["type"],
                        "location": best_match["location"],
                        "pid": None,
                        "message": f"Found green software: {best_match['name']} (at {best_match['location']})"
                    })

            # If still not found
            if not path:
                return parse_result({"error": f"App not found: {title}. May not be installed or needs manual path."})

        # Verify path exists
        if not os.path.exists(path):
            return parse_result({"error": f"Path not found: {path}"})

        # If directory, open in Explorer
        if os.path.isdir(path):
            subprocess.Popen(["explorer", path])
            return parse_result({"status": "ok", "action": "directory_opened", "path": path})

        # Launch executable
        cmd = [path] + app_args
        proc = subprocess.Popen(cmd)
        return parse_result({"status": "ok", "pid": proc.pid, "path": path})

    except Exception as e:
        return parse_result({"error": str(e)})
