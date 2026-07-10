"""Computer use tools - Screenshot and window management module.

截图策略：
- 默认保存到 memory/screenshots/（持久化）
- 截图用于定位/验证，操作成功后由 PostToolUse 钩子自动清理
- 操作失败时保留截图供调试
"""

import json
import os
import time
import ctypes
import ctypes.wintypes
import glob
from reasonix_computer_use.mcp_server import register_tool
from reasonix_computer_use.utils import parse_result


# 截图保存目录（持久化，插件包 memory/ 下）
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "memory", "screenshots")


def _get_screenshot_dir():
    """获取截图保存目录，不存在则创建。"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    return SCREENSHOT_DIR


@register_tool(
    name="computer_screenshot",
    description="""捕获屏幕截图。

三种模式：
- "full": 截取整个屏幕（默认）
- "window": 按窗口标题或 hwnd 截取特定窗口
- "region": 按坐标区域截取

截图默认保存到 memory/screenshots/ 目录（持久化），路径返回给 Agent 读取。""",
    schema={
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["full", "window", "region"],
                "default": "full",
                "description": "截图模式：full=全屏, window=指定窗口, region=区域"
            },
            "window_id": {
                "type": "string",
                "description":"窗口标识（window 模式）。可以是窗口标题（模糊匹配）或 hwnd（如 0x123456）。"
            },
            "region": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description":"左上角 X 坐标（虚拟屏幕坐标）"},
                    "y": {"type": "integer", "description":"左上角 Y 坐标（虚拟屏幕坐标）"},
                    "width": {"type": "integer", "description":"宽度（像素）"},
                    "height": {"type": "integer", "description":"高度（像素）"}
                },
                "required": ["x", "y", "width", "height"],
                "description":"截图区域（region 模式）。虚拟屏幕坐标，副屏可能为负值。"
            },
            "annotate": {
                "type": "boolean",
                "default": False,
                "description":"是否在截图上叠加 UI 树标注。"
            },
            "output_path": {
                "type": "string",
                "description":"自定义保存路径。省略则保存到 memory/screenshots/。"
            }
        }
    }
)
async def computer_screenshot(args: dict) -> str:
    """捕获屏幕截图。"""
    mode = args.get("mode", "full")
    output_path = args.get("output_path")
    annotate = args.get("annotate", False)
    
    try:
        import pyautogui
    except ImportError as e:
        return parse_result({"error": f"缺少依赖: {e}。执行: pip install pyautogui"})
    
    try:
        if mode == "full":
            screenshot = pyautogui.screenshot()
        elif mode == "window":
            window_id = args.get("window_id", "")
            screenshot = _capture_window(window_id)
        elif mode == "region":
            region = args.get("region", {})
            x, y = region.get("x", 0), region.get("y", 0)
            w, h = region.get("width", 100), region.get("height", 100)
            screenshot = pyautogui.screenshot(region=(x, y, w, h))
        else:
            return parse_result({"error": f"未知模式: {mode}"})
        
        # 保存到指定路径或默认 memory/screenshots/
        if output_path is None:
            screenshot_dir = _get_screenshot_dir()
            output_path = os.path.join(screenshot_dir, f"screenshot_{int(time.time())}.png")
        
        # 确保目录存在
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        screenshot.save(output_path, "PNG")
        
        return parse_result({
            "status": "ok",
            "mode": mode,
            "path": output_path,
            "width": screenshot.width,
            "height": screenshot.height,
            "annotate": annotate
        })
    except Exception as e:
        return parse_result({"error": str(e)})


@register_tool(
    name="computer_screenshot_cleanup",
    description="""清理截图文件。

默认清理超过 1 小时的旧截图。
如果指定了 path 则删除指定文件。
如果指定了 keep_recent_minutes 则保留最近 N 分钟的截图。""",
    schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description":"要删除的截图路径。省略则批量清理旧截图。"
            },
            "keep_recent_minutes": {
                "type": "integer",
                "default": 60,
                "description":"保留最近多少分钟内的截图（默认 60 分钟）。"
            }
        }
    }
)
async def computer_screenshot_cleanup(args: dict) -> str:
    """清理截图文件。"""
    path = args.get("path")
    keep_recent_minutes = args.get("keep_recent_minutes", 60)
    
    try:
        if path:
            # 删除指定文件
            if os.path.exists(path):
                os.remove(path)
                return parse_result({"status": "ok", "deleted": path})
            else:
                return parse_result({"status": "ok", "message": f"文件不存在: {path}"})
        else:
            # 批量清理旧截图
            screenshot_dir = _get_screenshot_dir()
            if not os.path.isdir(screenshot_dir):
                return parse_result({"status": "ok", "cleaned": 0, "message": "截图目录不存在"})
            
            now = time.time()
            keep_seconds = keep_recent_minutes * 60
            cleaned = 0
            
            for f in glob.glob(os.path.join(screenshot_dir, "screenshot_*.png")):
                try:
                    file_age = now - os.path.getmtime(f)
                    if file_age > keep_seconds:
                        os.remove(f)
                        cleaned += 1
                except OSError:
                    continue
            
            return parse_result({
                "status": "ok",
                "cleaned": cleaned,
                "message": f"已清理 {cleaned} 张超过 {keep_recent_minutes} 分钟的旧截图"
            })
    except Exception as e:
        return parse_result({"error": str(e)})


def _find_window_by_title(title: str) -> int | None:
    """按窗口标题模糊匹配查找 hwnd。"""
    result = []
    
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def enum_callback(hwnd, lParam):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buff = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
        if title.lower() in buff.value.lower():
            result.append(hwnd)
            return False  # 停止遍历
        return True
    
    ctypes.windll.user32.EnumWindows(enum_callback, 0)
    return result[0] if result else None


def _capture_window(window_id: str):
    """按标题或 hwnd 截取指定窗口。"""
    import pyautogui
    
    hwnd = None
    if window_id.startswith("0x") or window_id.isdigit():
        hwnd = int(window_id, 16) if window_id.startswith("0x") else int(window_id)
    else:
        hwnd = _find_window_by_title(window_id)
    
    if hwnd is None:
        raise ValueError(f"未找到窗口: {window_id}")
    
    # 获取窗口区域
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    
    # 置顶
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
    
    # 截图
    left, top = rect.left, rect.top
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    screenshot = pyautogui.screenshot(region=(left, top, width, height))
    return screenshot


@register_tool(
    name="computer_window_list",
    description="获取所有可见窗口列表。",
    schema={
        "type": "object",
        "properties": {
            "visible_only": {
                "type": "boolean",
                "default": True,
                "description":"是否只返回可见窗口。"
            },
            "min_width": {
                "type": "integer",
                "default": 10,
                "description":"最小窗口宽度，过滤掉不可见的工具窗口。"
            }
        }
    }
)
async def computer_window_list(args: dict) -> str:
    """获取所有可见窗口列表。"""
    visible_only = args.get("visible_only", True)
    min_width = args.get("min_width", 10)
    
    windows = []
    
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def enum_callback(hwnd, lParam):
        if visible_only and not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        
        buff = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value
        
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < min_width or height < 10:
            return True
        
        # 获取窗口类名
        class_buff = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, class_buff, 256)
        
        windows.append({
            "hwnd": hex(hwnd),
            "title": title,
            "class_name": class_buff.value,
            "rect": {
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom
            },
            "width": width,
            "height": height
        })
        return True
    
    ctypes.windll.user32.EnumWindows(enum_callback, 0)
    
    return parse_result({
        "status": "ok",
        "count": len(windows),
        "windows": windows
    })


@register_tool(
    name="computer_window_activate",
    description="激活（置顶）指定窗口。",
    schema={
        "type": "object",
        "properties": {
            "window_id": {
                "type": "string",
                "description":"窗口标识：标题（模糊匹配）、hwnd（十六进制）或 class_name。"
            },
            "method": {
                "type": "string",
                "enum": ["title", "hwnd", "class"],
                "default": "title",
                "description":"匹配方式：title=标题模糊, hwnd=精确句柄, class=窗口类名。"
            }
        },
        "required": ["window_id"]
    }
)
async def computer_window_activate(args: dict) -> str:
    """激活指定窗口。"""
    window_id = args.get("window_id", "")
    method = args.get("method", "title")
    
    hwnd = None
    found_title = ""
    
    if method == "hwnd":
        hwnd = int(window_id, 16) if window_id.startswith("0x") else int(window_id)
    else:
        target = window_id.lower()
        
        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def enum_callback(hwnd_, lParam):
            nonlocal hwnd, found_title
            if not ctypes.windll.user32.IsWindowVisible(hwnd_):
                return True
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd_)
            if length == 0:
                return True
            buff = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd_, buff, length + 1)
            
            match = False
            if method == "title":
                match = target in buff.value.lower()
            else:
                class_buff = ctypes.create_unicode_buffer(256)
                ctypes.windll.user32.GetClassNameW(hwnd_, class_buff, 256)
                match = target == class_buff.value.lower()
            
            if match:
                hwnd = hwnd_
                found_title = buff.value
                return False  # 停止
            return True
        
        ctypes.windll.user32.EnumWindows(enum_callback, 0)
    
    if hwnd is None:
        return parse_result({"error": f"未找到窗口: {window_id}"})
    
    # 如果是最小化状态则恢复
    if ctypes.windll.user32.IsIconic(hwnd):
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.1)
    
    return parse_result({
        "status": "ok",
        "hwnd": hex(hwnd),
        "title": found_title
    })
