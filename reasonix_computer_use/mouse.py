"""Computer use tools - Mouse operations module."""

import json
import time
import ctypes
import ctypes.wintypes
from reasonix_computer_use.mcp_server import register_tool
from reasonix_computer_use.utils import parse_result
from reasonix_computer_use.windows import physical_pixel_context


# Constants for mouse events
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000


@register_tool(
    name="computer_mouse_move",
    description="""Low-level fallback: move the cursor in physical virtual-screen pixels.

Coordinates are in virtual screen pixels (supports multi-monitor).
- The origin (0, 0) is the top-left corner of the primary monitor.
- Secondary monitor positions may extend to negative X (left of primary) or
  values beyond primary monitor width (right of primary).
- Use computer_window_list to get the rect of a window, then move to its center.

Parameters:
- x: target X coordinate
- y: target Y coordinate
- duration: time in seconds for the move (default 0.1). 0 = instant.

After moving, a small delay is added for the OS to process the move.
""",
    schema={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "Target X coordinate (virtual screen coordinates)."},
            "y": {"type": "integer", "description": "Target Y coordinate (virtual screen coordinates)."},
            "duration": {
                "type": "number",
                "default": 0.1,
                "description": "Move duration in seconds. 0 = instant movement."
            }
        },
        "required": ["x", "y"]
    }
)
async def computer_mouse_move(args: dict) -> str:
    """Move mouse to absolute coordinates."""
    x = args.get("x", 0)
    y = args.get("y", 0)
    duration = args.get("duration", 0.1)
    
    try:
        with physical_pixel_context():
            if not ctypes.windll.user32.SetCursorPos(x, y):
                raise ctypes.WinError()
            actual = ctypes.wintypes.POINT()
            if not ctypes.windll.user32.GetCursorPos(ctypes.byref(actual)):
                raise ctypes.WinError()
            if (actual.x, actual.y) != (x, y):
                raise OSError(f"鼠标物理坐标校验失败: requested=({x},{y}), actual=({actual.x},{actual.y})")
        time.sleep(max(0.05, duration))
        return parse_result({
            "status": "ok",
            "x": x,
            "y": y
        })
    except Exception as e:
        return parse_result({"error": str(e)})


@register_tool(
    name="computer_mouse_click",
    description="""Low-level fallback: click at the current or supplied physical coordinates.

Parameters:
- button: "left" (default), "right", "middle"
- click_type: "single" (default), "double"
- x, y: optional coordinates. If provided, moves cursor first then clicks.

Use computer_find_element to locate an element, get its bounding_rectangle center,
then move to those coordinates and click.
""",
    schema={
        "type": "object",
        "properties": {
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "default": "left",
                "description": "Mouse button to click."
            },
            "click_type": {
                "type": "string",
                "enum": ["single", "double"],
                "default": "single",
                "description": "Single or double click."
            },
            "x": {"type": "integer", "description": "Optional X coordinate. If provided, moves cursor first."},
            "y": {"type": "integer", "description": "Optional Y coordinate. If provided, moves cursor first."}
        }
    }
)
async def computer_mouse_click(args: dict) -> str:
    """Click mouse button."""
    button = args.get("button", "left")
    click_type = args.get("click_type", "single")
    x = args.get("x")
    y = args.get("y")
    
    try:
        # Move cursor first if coordinates provided
        if x is not None and y is not None:
            with physical_pixel_context():
                if not ctypes.windll.user32.SetCursorPos(x, y):
                    raise ctypes.WinError()
                actual = ctypes.wintypes.POINT()
                if not ctypes.windll.user32.GetCursorPos(ctypes.byref(actual)):
                    raise ctypes.WinError()
                if (actual.x, actual.y) != (x, y):
                    raise OSError("鼠标未到达请求的物理像素坐标")
            time.sleep(0.05)
        
        # Determine button flags
        if button == "left":
            down_flag = MOUSEEVENTF_LEFTDOWN
            up_flag = MOUSEEVENTF_LEFTUP
        elif button == "right":
            down_flag = MOUSEEVENTF_RIGHTDOWN
            up_flag = MOUSEEVENTF_RIGHTUP
        elif button == "middle":
            down_flag = MOUSEEVENTF_MIDDLEDOWN
            up_flag = MOUSEEVENTF_MIDDLEUP
        else:
            return parse_result({"error": f"Unknown button: {button}"})
        
        # Send click
        ctypes.windll.user32.mouse_event(down_flag, 0, 0, 0, 0)
        time.sleep(0.02)
        ctypes.windll.user32.mouse_event(up_flag, 0, 0, 0, 0)
        
        if click_type == "double":
            time.sleep(0.05)
            ctypes.windll.user32.mouse_event(down_flag, 0, 0, 0, 0)
            time.sleep(0.02)
            ctypes.windll.user32.mouse_event(up_flag, 0, 0, 0, 0)
        
        time.sleep(0.05)
        
        return parse_result({
            "status": "ok",
            "button": button,
            "click_type": click_type
        })
    except Exception as e:
        return parse_result({"error": str(e)})


@register_tool(
    name="computer_mouse_scroll",
    description="""Low-level fallback: scroll the mouse wheel.

Parameters:
- direction: "up" (default) or "down"
- lines: number of lines to scroll (default 3). Each line ≈ 120 delta.

Most applications interpret a delta of 120 as one scroll "tick".
""",
    schema={
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["up", "down"],
                "default": "up",
                "description": "Scroll direction."
            },
            "lines": {
                "type": "integer",
                "default": 3,
                "description": "Number of scroll lines. Default 3. Each line ≈ 120 delta."
            }
        }
    }
)
async def computer_mouse_scroll(args: dict) -> str:
    """Scroll mouse wheel."""
    direction = args.get("direction", "up")
    lines = args.get("lines", 3)
    
    try:
        delta = lines * 120 if direction == "up" else -lines * 120
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
        time.sleep(0.05)
        
        return parse_result({
            "status": "ok",
            "direction": direction,
            "lines": lines,
            "delta": delta
        })
    except Exception as e:
        return parse_result({"error": str(e)})
