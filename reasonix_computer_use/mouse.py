"""Computer use tools - Mouse operations module.

跨平台支持：Windows/macOS/Linux via pynput。
"""

import json
import time
from reasonix_computer_use.mcp_server import register_tool
from reasonix_computer_use.utils import parse_result
from reasonix_computer_use.platform_backend import get_mouse


@register_tool(
    name="computer_mouse_move",
    description="""移动鼠标到屏幕物理坐标。

坐标是虚拟屏幕像素（支持多显示器）：
- 原点 (0, 0) 是主显示器左上角
- 副显示器可能在主显示器左侧（X 为负值）或右侧

参数：
- x: X 坐标
- y: Y 坐标
- duration: 移动时间（秒，默认 0.1）。0 = 瞬间移动。
""",
    schema={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X 坐标（虚拟屏幕坐标）"},
            "y": {"type": "integer", "description": "Y 坐标（虚拟屏幕坐标）"},
            "duration": {
                "type": "number",
                "default": 0.1,
                "description": "移动时间（秒）。0 = 瞬间移动"
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
        mouse = get_mouse()
        mouse.move(x, y)
        time.sleep(max(0.05, duration))
        return parse_result({"status": "ok", "x": x, "y": y})
    except Exception as e:
        return parse_result({"error": str(e)})


@register_tool(
    name="computer_mouse_click",
    description="""在指定或当前坐标点击鼠标。

参数：
- button: "left"（默认）、"right"、"middle"
- click_type: "single"（默认）、"double"
- x, y: 可选坐标。如果提供，先移动再点击。
""",
    schema={
        "type": "object",
        "properties": {
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "default": "left",
                "description": "鼠标按键"
            },
            "click_type": {
                "type": "string",
                "enum": ["single", "double"],
                "default": "single",
                "description": "单击或双击"
            },
            "x": {"type": "integer", "description": "可选 X 坐标。如果提供，先移动"},
            "y": {"type": "integer", "description": "可选 Y 坐标。如果提供，先移动"}
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
        mouse = get_mouse()
        if x is not None and y is not None:
            mouse.move(x, y)
            time.sleep(0.05)

        double = click_type == "double"
        mouse.click(button, double)
        time.sleep(0.05)

        return parse_result({"status": "ok", "button": button, "click_type": click_type})
    except Exception as e:
        return parse_result({"error": str(e)})


@register_tool(
    name="computer_mouse_scroll",
    description="""滚动鼠标滚轮。

参数：
- direction: "up"（默认）或 "down"
- lines: 滚动行数（默认 3）
""",
    schema={
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["up", "down"],
                "default": "up",
                "description": "滚动方向"
            },
            "lines": {
                "type": "integer",
                "default": 3,
                "description": "滚动行数（默认 3）"
            }
        }
    }
)
async def computer_mouse_scroll(args: dict) -> str:
    """Scroll mouse wheel."""
    direction = args.get("direction", "up")
    lines = args.get("lines", 3)

    try:
        mouse = get_mouse()
        mouse.scroll(direction, lines)
        time.sleep(0.05)
        return parse_result({"status": "ok", "direction": direction, "lines": lines})
    except Exception as e:
        return parse_result({"error": str(e)})
