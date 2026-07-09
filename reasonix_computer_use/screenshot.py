"""Computer use tools - Screenshot and window management module."""

import json
import os
import time
import ctypes
import ctypes.wintypes
from reasonix_computer_use.mcp_server import register_tool
from reasonix_computer_use.utils import parse_result


@register_tool(
    name="computer_screenshot",
    description="""Capture a screenshot of the screen.

Three modes:
- "full": Capture the entire screen (default). Returns PNG image as base64 and file path.
- "window": Capture a specific window by window title or hwnd.
- "region": Capture a rectangular region defined by x, y, width, height.

The screenshot is saved to a temp file, and the path is returned so the agent can read it with read_file tool.
""",
    schema={
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["full", "window", "region"],
                "default": "full",
                "description": "Screenshot mode. 'full' = entire screen, 'window' = specific window, 'region' = rectangular area."
            },
            "window_id": {
                "type": "string",
                "description": "Window identifier for 'window' mode. Can be window title (partial match) or hwnd (hex string like '0x123456')."
            },
            "region": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Left edge X coordinate (virtual screen coordinates)"},
                    "y": {"type": "integer", "description": "Top edge Y coordinate (virtual screen coordinates)"},
                    "width": {"type": "integer", "description": "Region width in pixels"},
                    "height": {"type": "integer", "description": "Region height in pixels"}
                },
                "required": ["x", "y", "width", "height"],
                "description": "Rectangle for 'region' mode. Uses virtual screen coordinates (may be negative on secondary monitors)."
            },
            "annotate": {
                "type": "boolean",
                "default": False,
                "description": "If true, overlay UI tree element bounding boxes and reference IDs on the screenshot."
            },
            "output_path": {
                "type": "string",
                "description": "Absolute path to save the PNG. If omitted, a temp file is created."
            }
        }
    }
)
async def computer_screenshot(args: dict) -> str:
    """Capture a screenshot."""
    mode = args.get("mode", "full")
    output_path = args.get("output_path")
    annotate = args.get("annotate", False)
    
    try:
        import pyautogui
    except ImportError as e:
        return parse_result({"error": f"Missing dependency: {e}. Install with: pip install pyautogui"})
    
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
            return parse_result({"error": f"Unknown mode: {mode}"})
        
        # Save to file
        if output_path is None:
            import tempfile
            output_path = os.path.join(tempfile.gettempdir(), f"reasonix_screenshot_{int(time.time())}.png")
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


def _find_window_by_title(title: str) -> int | None:
    """Find window hwnd by partial title match. Returns hwnd or None."""
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
            return False  # Stop enumeration
        return True
    
    ctypes.windll.user32.EnumWindows(enum_callback, 0)
    return result[0] if result else None


def _capture_window(window_id: str):
    """Capture a specific window by title or hwnd."""
    import pyautogui
    
    hwnd = None
    if window_id.startswith("0x") or window_id.isdigit():
        hwnd = int(window_id, 16) if window_id.startswith("0x") else int(window_id)
    else:
        hwnd = _find_window_by_title(window_id)
    
    if hwnd is None:
        raise ValueError(f"Window not found: {window_id}")
    
    # Get window rect
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    
    # Bring to foreground
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
    
    # Capture
    left, top = rect.left, rect.top
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    screenshot = pyautogui.screenshot(region=(left, top, width, height))
    return screenshot


@register_tool(
    name="computer_window_list",
    description="""List all visible windows on the desktop.

Returns a list of window metadata:
- hwnd: unique window identifier (hex string)
- title: window title text
- class_name: window class name (useful for identifying apps)
- rect: {left, top, right, bottom} — screen coordinates of the window
- width, height: window dimensions in pixels

Useful for finding a target window before calling computer_screenshot or computer_window_activate.
""",
    schema={
        "type": "object",
        "properties": {
            "visible_only": {
                "type": "boolean",
                "default": True,
                "description": "If true (default), only return visible windows."
            },
            "min_width": {
                "type": "integer",
                "default": 10,
                "description": "Minimum window width to include. Filters out tiny invisible tool windows."
            }
        }
    }
)
async def computer_window_list(args: dict) -> str:
    """List all visible windows."""
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
        
        # Get class name
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
    description="""Activate (bring to foreground) a specified window.

After activating, the window is ready for mouse/keyboard input.

Parameters:
- window_id: window title (partial match), hwnd (hex string), or class_name
- method: how to match the window:
  - "title" (default): partial match on window title
  - "hwnd": exact hwnd match
  - "class": exact class_name match

Returns the activated window's hwnd and title on success.
""",
    schema={
        "type": "object",
        "properties": {
            "window_id": {
                "type": "string",
                "description": "Window identifier — title (partial), hwnd (hex), or class_name."
            },
            "method": {
                "type": "string",
                "enum": ["title", "hwnd", "class"],
                "default": "title",
                "description": "How to match the window."
            }
        },
        "required": ["window_id"]
    }
)
async def computer_window_activate(args: dict) -> str:
    """Activate a window."""
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
                return False  # Stop
            return True
        
        ctypes.windll.user32.EnumWindows(enum_callback, 0)
    
    if hwnd is None:
        return parse_result({"error": f"Window not found: {window_id}"})
    
    # Show if minimized
    if ctypes.windll.user32.IsIconic(hwnd):
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.1)
    
    return parse_result({
        "status": "ok",
        "hwnd": hex(hwnd),
        "title": found_title
    })
