"""Computer use tools - Keyboard operations module."""

import json
import time
import ctypes
import ctypes.wintypes
from ctypes import wintypes
from reasonix_computer_use.mcp_server import register_tool
from reasonix_computer_use.utils import parse_result


# Virtual Key Codes (Windows)
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # ALT
VK_PAUSE = 0x13
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21  # PAGE UP
VK_NEXT = 0x22   # PAGE DOWN
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_SNAPSHOT = 0x2C  # PRINT SCREEN
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_NUMPAD0 = 0x60
VK_NUMPAD9 = 0x69
VK_F1 = 0x70
VK_F12 = 0x7B

VK_MAP = {
    "enter": VK_RETURN,
    "return": VK_RETURN,
    "tab": VK_TAB,
    "backspace": VK_BACK,
    "delete": VK_DELETE,
    "escape": VK_ESCAPE,
    "esc": VK_ESCAPE,
    "space": VK_SPACE,
    "up": VK_UP,
    "down": VK_DOWN,
    "left": VK_LEFT,
    "right": VK_RIGHT,
    "home": VK_HOME,
    "end": VK_END,
    "pageup": VK_PRIOR,
    "pagedown": VK_NEXT,
    "insert": VK_INSERT,
    "shift": VK_SHIFT,
    "ctrl": VK_CONTROL,
    "alt": VK_MENU,
    "win": VK_LWIN,
    "meta": VK_LWIN,
    "f1": VK_F1,
    "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": VK_F12,
}

MODIFIER_KEYS = {"shift", "ctrl", "alt", "win", "meta"}


# ─── Pre-initialized ctypes structures (module level) ────────────────────────

INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT),
    ]


# Pre-allocate reusable input structures
def _create_unicode_input(char_code: int, key_up: bool = False) -> INPUT:
    """Create an INPUT structure for Unicode key event."""
    flags = KEYEVENTF_UNICODE
    if key_up:
        flags |= KEYEVENTF_KEYUP
    
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp._input.ki.wVk = 0
    inp._input.ki.wScan = char_code
    inp._input.ki.dwFlags = flags
    inp._input.ki.time = 0
    inp._input.ki.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))
    return inp


def _send_key(vk_code: int, key_up: bool = False):
    """Send a key event using keybd_event (legacy but reliable)."""
    flags = 0
    if key_up:
        flags |= 0x0002  # KEYEVENTF_KEYUP
    ctypes.windll.user32.keybd_event(0, vk_code, flags, 0)


@register_tool(
    name="computer_keyboard_press",
    description="""Low-level fallback: press a key or key combination.

Parameters:
- key: key name (e.g., "Enter", "Tab", "Escape", "Space", "F1", "Delete")
- modifiers: list of modifier keys (e.g., ["ctrl"], ["ctrl", "shift"])

Key names (case-insensitive):
- Navigation: Enter, Tab, Backspace, Delete, Escape, Space
- Arrows: Up, Down, Left, Right
- Page: Home, End, PageUp, PageDown
- Modifiers: Shift, Ctrl, Alt, Win/Meta
- Function: F1-F12

Common combinations:
- Ctrl+C: key="c", modifiers=["ctrl"]
- Ctrl+V: key="v", modifiers=["ctrl"]
- Alt+Tab: key="tab", modifiers=["alt"]
- Win+D: key="d", modifiers=["win"]

First sends modifier key-downs, then the main key (down+up), then releases modifiers.
""",
    schema={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Key name (Enter, Tab, Escape, Space, Up, Ctrl, etc.)"
            },
            "modifiers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Modifier keys (ctrl, alt, shift, win).",
                "default": []
            }
        },
        "required": ["key"]
    }
)
async def computer_keyboard_press(args: dict) -> str:
    """Press a key or key combination."""
    key = args.get("key", "").lower().strip()
    modifiers = args.get("modifiers", [])
    
    try:
        # Resolve virtual key code
        if key in VK_MAP:
            vk_code = VK_MAP[key]
        elif len(key) == 1 and key.isalnum():
            # Single character: use VkKeyScan
            vk_code = ctypes.windll.user32.VkKeyScanW(ord(key.upper()))
            if vk_code == -1:
                return parse_result({"error": f"Cannot resolve key: {key}"})
            vk_code = vk_code & 0xFF
        else:
            return parse_result({"error": f"Unknown key: {key}"})
        
        # Press modifiers
        for mod in modifiers:
            mod_lower = mod.lower().strip()
            if mod_lower in VK_MAP:
                _send_key(VK_MAP[mod_lower], key_up=False)
        
        time.sleep(0.02)
        
        # Press and release main key
        _send_key(vk_code, key_up=False)
        time.sleep(0.02)
        _send_key(vk_code, key_up=True)
        
        time.sleep(0.02)
        
        # Release modifiers
        for mod in modifiers:
            mod_lower = mod.lower().strip()
            if mod_lower in VK_MAP:
                _send_key(VK_MAP[mod_lower], key_up=True)
        
        time.sleep(0.05)
        
        return parse_result({
            "status": "ok",
            "key": key,
            "modifiers": modifiers
        })
    except Exception as e:
        return parse_result({"error": str(e)})


@register_tool(
    name="computer_keyboard_type",
    description="""Low-level fallback: type Unicode text into the focused control.

Supports any Unicode text including Chinese, Japanese, and special characters.
The text is typed using SendInput with KEYEVENTF_UNICODE flag, which is more
reliable than keybd_event for international characters.

Parameters:
- text: the text to type
- interval: delay between keystroke in seconds (default 0.02)

This method does NOT require the active window to be in English input mode.
It sends Unicode characters directly.
""",
    schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to type (supports Unicode including CJK characters)."
            },
            "interval": {
                "type": "number",
                "default": 0.02,
                "description": "Delay between characters in seconds. Default 0.02s."
            }
        },
        "required": ["text"]
    }
)
async def computer_keyboard_type(args: dict) -> str:
    """Type text character by character using Unicode input."""
    text = args.get("text", "")
    interval = args.get("interval", 0.02)
    
    try:
        for char in text:
            char_code = ord(char)
            
            # Key down
            inp = _create_unicode_input(char_code, key_up=False)
            ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            
            # Key up
            inp = _create_unicode_input(char_code, key_up=True)
            ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            
            time.sleep(interval)
        
        return parse_result({
            "status": "ok",
            "text_length": len(text)
        })
    except Exception as e:
        return parse_result({"error": str(e)})
