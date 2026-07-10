"""Computer use tools - Application discover and launch module.

新逻辑：
1. 首次执行 → 执行快速扫描（Python 直接读取注册表，无 PowerShell GBK 编码问题）
2. 后续执行 → 读取缓存文件
3. 搜索不到 → 重新扫描
4. 仍找不到 → 检测绿色软件（桌面快捷方式、常见目录）

使用 Python winreg 直接读取注册表，比 PowerShell 快且无编码问题。
"""

import json
import datetime
import os
import subprocess
import winreg
from pathlib import Path
from reasonix_computer_use.mcp_server import register_tool
from reasonix_computer_use.utils import parse_result


CACHE_FILENAME = "applist.md"


# PowerShell 命令只用于获取 Store 应用（无法用 winreg 读取）
_APPX_COMMAND = r'''
$ErrorActionPreference = 'SilentlyContinue'
$OutputEncoding = [System.Text.Encoding]::UTF8
Get-AppxPackage | Select-Object PackageFullName, InstallLocation | Sort-Object PackageFullName | ConvertTo-Csv -NoTypeInformation -Delimiter "`t"
'''


def _get_memory_dir():
    """获取 memory 目录路径（相对于项目根目录）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "memory")


def _get_cache_path():
    """获取缓存文件完整路径。"""
    return os.path.join(_get_memory_dir(), CACHE_FILENAME)


def _run_ps_utf8(cmd):
    """执行 PowerShell 命令并返回 UTF-8 字符串。"""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=False, timeout=30
    )
    return proc.stdout.decode('utf-8', errors='replace') if proc.stdout else ""


def _scan_registry_with_winreg():
    """使用 Python winreg 直接读取注册表（快 + 无编码问题）。"""
    apps = []
    
    # 3 个注册表路径
    paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    
    seen = set()  # 去重
    
    for hive, path in paths:
        try:
            key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    subkey = winreg.OpenKey(key, subkey_name)
                    
                    display_name = None
                    try:
                        display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                    except FileNotFoundError:
                        pass
                    
                    if not display_name or not display_name.strip():
                        winreg.CloseKey(subkey)
                        continue
                    
                    # 去重
                    if display_name.lower() in seen:
                        winreg.CloseKey(subkey)
                        continue
                    seen.add(display_name.lower())
                    
                    app = {"DisplayName": display_name}
                    
                    try:
                        app["DisplayVersion"] = winreg.QueryValueEx(subkey, "DisplayVersion")[0]
                    except (FileNotFoundError, OSError):
                        pass
                    
                    try:
                        app["InstallLocation"] = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                    except (FileNotFoundError, OSError):
                        pass
                    
                    apps.append(app)
                    winreg.CloseKey(subkey)
                except (OSError, PermissionError):
                    continue
            
            winreg.CloseKey(key)
        except (FileNotFoundError, PermissionError, OSError):
            continue
    
    # 按名称排序
    apps.sort(key=lambda x: x.get("DisplayName", "").lower())
    return apps


def _scan_appx_with_powershell():
    """使用 PowerShell 获取 Microsoft Store 应用（UTF-8 解码）。"""
    result = _run_ps_utf8(_APPX_COMMAND)
    if not result.strip():
        return []
    
    apps = []
    for line in result.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip().startswith('"PackageFullName"'):
            continue
        if len(parts) >= 2:
            pkg = parts[0].strip().strip('"')
            loc = parts[1].strip().strip('"') if len(parts) > 1 else ""
            apps.append({
                "PackageFullName": pkg,
                "InstallLocation": loc
            })
    
    return apps


def _scan_apps():
    """快速扫描已安装应用（winreg + PowerShell）。"""
    registry_apps = _scan_registry_with_winreg()
    appx_apps = _scan_appx_with_powershell()
    return registry_apps, appx_apps


def _format_apps_markdown(registry_apps, appx_apps):
    """将扫描结果格式化为 Markdown 表格。"""
    lines = []
    
    if registry_apps:
        lines.append("## 注册表应用\n")
        lines.append("| 名称 | 版本 | 安装位置 |")
        lines.append("|---|---|---|")
        for app in registry_apps:
            name = app.get("DisplayName", "").replace("|", "\\|") or ""
            ver = app.get("DisplayVersion", "").replace("|", "\\|") or ""
            loc = app.get("InstallLocation", "").replace("|", "\\|") or ""
            lines.append(f"| {name} | {ver} | {loc} |")
        lines.append("")
    
    if appx_apps:
        lines.append("## Microsoft Store 应用\n")
        lines.append("| 包名 | 安装位置 |")
        lines.append("|---|---|")
        for app in appx_apps:
            name = app.get("PackageFullName", "").replace("|", "\\|") or ""
            loc = app.get("InstallLocation", "").replace("|", "\\|") or ""
            lines.append(f"| {name} | {loc} |")
        lines.append("")
    
    return "\n".join(lines)


def _scan_registry_with_powershell():
    """执行扫描并生成 Markdown 格式内容（加速版，使用 winreg + UTF-8 PS）。"""
    registry_apps, appx_apps = _scan_apps()
    md_content = _format_apps_markdown(registry_apps, appx_apps)
    
    total = len(registry_apps) + len(appx_apps)
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    content = f"""# 已安装应用列表

> 共 {total} 个应用
> 注册表: {len(registry_apps)} | Microsoft Store: {len(appx_apps)}
> 生成时间: {timestamp}

{md_content}
"""
    return content


def _write_cache(content):
    """将扫描结果写入缓存文件（覆盖模式，UTF-8 编码）。"""
    cache_path = Path(_get_cache_path())
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"# 已安装应用列表\n\n> 生成时间: {timestamp}\n\n"
    
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(content)
    
    return str(cache_path)


def _read_cache():
    """从缓存文件读取应用列表（UTF-8 编码）。"""
    cache_path = Path(_get_cache_path())
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _search_in_cache(cache_content, search_term):
    """在缓存内容中搜索应用（Markdown 表格格式）。"""
    if not cache_content:
        return []
    
    results = []
    search_lower = search_term.lower()
    
    for line in cache_content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("|---"):
            continue
        if stripped.startswith("| 名称") or stripped.startswith("| 包名") or stripped.startswith("| DisplayName"):
            continue
        
        if search_lower in stripped.lower():
            results.append(stripped)
    
    return results


def _resolve_lnk_target(lnk_path):
    """解析 .lnk 快捷方式文件，返回目标路径（纯 Python）。"""
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
    """检测绿色软件。检查常见目录和快捷方式。"""
    search_lower = app_name.lower()
    findings = []
    
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
    """从缓存内容中提取指定应用的安装路径（Markdown 表格格式）。"""
    search_lower = app_name.lower()
    
    for line in cache_content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("|---"):
            continue
        if stripped.startswith("| 名称") or stripped.startswith("| 包名") or stripped.startswith("| DisplayName"):
            continue
        
        if search_lower in stripped.lower():
            parts = [p.strip() for p in stripped.split("|")]
            if len(parts) >= 4:
                install_path = parts[3].strip()
                if install_path and os.path.isdir(install_path):
                    return install_path
    
    return None


@register_tool(
    name="computer_app_list",
    description="获取已安装应用列表。\n\n逻辑：\n- 首次执行：用 Python winreg 扫描注册表 + PowerShell 扫描 Store\n- 结果缓存到 memory/applist.md（Markdown 表格）\n- 后续执行：从缓存读取（快速）\n- 搜索不到：自动刷新缓存\n- 仍找不到：检测绿色软件\n\n参数：\n- search: 按名称搜索（可选）\n- refresh: 强制刷新缓存（默认 false）\n",
    schema={
        "type": "object",
        "properties": {
            "search": {"type": "string", "description": "按名称搜索"},
            "refresh": {"type": "boolean", "default": False, "description": "强制重新扫描并覆盖缓存"}
        }
    }
)
async def computer_app_list(args: dict) -> str:
    """获取已安装应用列表。"""
    search = args.get("search", "").strip()
    force_refresh = args.get("refresh", False)
    
    cache_content = None
    
    # 优先读取缓存（除非强制刷新）
    if not force_refresh:
        cache_content = _read_cache()
    
    # 无缓存或强制刷新时，执行快速扫描
    if cache_content is None:
        scan_result = _scan_registry_with_powershell()
        cache_path = _write_cache(scan_result)
        cache_content = scan_result
    
    # 搜索过滤
    if search:
        matches = _search_in_cache(cache_content, search)
        # 如果缓存中没找到，尝试刷新
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
    
    # 返回完整缓存内容
    return parse_result({
        "status": "ok",
        "content": cache_content,
        "cache_file": CACHE_FILENAME
    })


@register_tool(
    name="computer_app_launch",
    description="启动应用程序。\n\n查找逻辑（按顺序）：\n1. 如果提供了 path，直接启动\n2. 如果提供了 title，在 memory/applist.md 缓存中搜索\n3. 如果缓存中找不到，自动重新扫描并更新缓存\n4. 如果仍然找不到，尝试绿色软件检测（桌面快捷方式、常见目录）\n5. 如果还没有，返回未找到\n\n参数：\n- path: 可执行文件绝对路径或 .lnk 快捷方式\n- args: 命令行参数\n- title: 如果 path 为空，按应用名称查找\n",
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "可执行文件路径或 .lnk 快捷方式"},
            "args": {"type": "array", "items": {"type": "string"}, "default": [], "description": "命令行参数"},
            "title": {"type": "string", "description": "如果 path 为空，按应用名称查找"}
        }
    }
)
async def computer_app_launch(args: dict) -> str:
    """启动应用程序。"""
    path = args.get("path", "")
    app_args = args.get("args", [])
    title = args.get("title", "")
    
    try:
        # 如果没有 path 但有 title，查找应用
        if not path and title:
            # 第一步：在缓存中搜索
            cache_content = _read_cache()
            if cache_content:
                install_path = _extract_install_path(cache_content, title)
                if install_path:
                    for f in os.listdir(install_path):
                        if f.lower().endswith(".exe"):
                            path = os.path.join(install_path, f)
                            break
            
            # 第二步：如果找不到，重新扫描并更新缓存
            if not path:
                scan_result = _scan_registry_with_powershell()
                cache_path = _write_cache(scan_result)
                install_path = _extract_install_path(scan_result, title)
                if install_path:
                    for f in os.listdir(install_path):
                        if f.lower().endswith(".exe"):
                            path = os.path.join(install_path, f)
                            break
            
            # 第三步：如果仍然找不到，尝试绿色软件检测
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
            
            # 如果还是找不到
            if not path:
                return parse_result({
                    "error": f"App not found: {title}. May not be installed or needs manual path."
                })
        
        # 验证路径存在
        if not os.path.exists(path):
            return parse_result({"error": f"Path not found: {path}"})
        
        # 如果是目录，打开目录
        if os.path.isdir(path):
            subprocess.Popen(["explorer", path])
            return parse_result({
                "status": "ok",
                "action": "directory_opened",
                "path": path
            })
        
        # 启动可执行文件
        cmd = [path] + app_args
        proc = subprocess.Popen(cmd)
        return parse_result({
            "status": "ok",
            "pid": proc.pid,
            "path": path
        })
    except Exception as e:
        return parse_result({"error": str(e)})
