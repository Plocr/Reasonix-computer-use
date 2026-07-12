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


ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", INPUT_UNION)]


_SendInput = ctypes.windll.user32.SendInput
_SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_SendInput.restype = wintypes.UINT

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


# Pre-allocate reusable input structures
def _create_unicode_input(char_code: int, key_up: bool = False) -> INPUT:
    """Create an INPUT structure for Unicode key event."""
    flags = KEYEVENTF_UNICODE
    if key_up:
        flags |= KEYEVENTF_KEYUP
    
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = KEYBDINPUT(0, char_code, flags, 0, 0)
    return inp


def _unicode_units(text: str) -> list[int]:
    raw = text.encode("utf-16-le", errors="surrogatepass")
    return [int.from_bytes(raw[index:index + 2], "little") for index in range(0, len(raw), 2)]


def send_unicode_text(text: str, interval: float = 0.0) -> int:
    """Inject UTF-16 key events and fail if Windows accepts fewer than requested."""
    sent_units = 0
    for unit in _unicode_units(text):
        events = (INPUT * 2)(_create_unicode_input(unit), _create_unicode_input(unit, key_up=True))
        inserted = int(_SendInput(2, events, ctypes.sizeof(INPUT)))
        if inserted != 2:
            raise ctypes.WinError(ctypes.get_last_error() or 5)
        sent_units += 1
        if interval:
            time.sleep(interval)
    return sent_units


def _send_key(vk_code: int, key_up: bool = False):
    """Send a key event using keybd_event (legacy but reliable)."""
    flags = 0
    if key_up:
        flags |= 0x0002  # KEYEVENTF_KEYUP
    # keybd_event expects the virtual-key code first. The old reversed order
    # silently emitted VK=0, which made Enter and shortcuts appear successful.
    ctypes.windll.user32.keybd_event(vk_code, 0, flags, 0)


def _open_clipboard(retries: int = 20) -> None:
    for _ in range(retries):
        if ctypes.windll.user32.OpenClipboard(None):
            return
        time.sleep(0.01)
    raise ctypes.WinError()


def _set_clipboard_text(text: str) -> None:
    encoded = text.encode("utf-16-le") + b"\x00\x00"
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    ctypes.windll.user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    ctypes.windll.user32.SetClipboardData.restype = wintypes.HANDLE
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
    if not handle:
        raise ctypes.WinError()
    locked = kernel32.GlobalLock(handle)
    if not locked:
        kernel32.GlobalFree(handle)
        raise ctypes.WinError()
    try:
        ctypes.memmove(locked, encoded, len(encoded))
    finally:
        kernel32.GlobalUnlock(handle)
    _open_clipboard()
    try:
        if not ctypes.windll.user32.EmptyClipboard():
            raise ctypes.WinError()
        if not ctypes.windll.user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise ctypes.WinError()
        handle = None  # The clipboard owns the allocation after success.
    finally:
        ctypes.windll.user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)


def _release_com_pointer(pointer: ctypes.c_void_p) -> None:
    if not pointer.value:
        return
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
    release(pointer)


def paste_unicode_text(text: str) -> bool:
    """Paste once while preserving the complete pre-existing OLE clipboard."""
    ole32 = ctypes.OleDLL("ole32")
    ole32.OleInitialize.argtypes = [ctypes.c_void_p]
    ole32.OleInitialize.restype = ctypes.c_long
    ole32.OleGetClipboard.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    ole32.OleGetClipboard.restype = ctypes.c_long
    ole32.OleSetClipboard.argtypes = [ctypes.c_void_p]
    ole32.OleSetClipboard.restype = ctypes.c_long
    initialized = ole32.OleInitialize(None)
    original = ctypes.c_void_p()
    got_original = ole32.OleGetClipboard(ctypes.byref(original)) >= 0 and bool(original.value)
    if not got_original:
        if initialized in (0, 1):
            ole32.OleUninitialize()
        raise OSError("无法保存当前剪贴板，已取消粘贴回退")
    restored = False
    try:
        _set_clipboard_text(text)
        _send_key(VK_CONTROL, key_up=False)
        _send_key(0x41, key_up=False)
        _send_key(0x41, key_up=True)
        _send_key(VK_CONTROL, key_up=True)
        time.sleep(0.03)
        _send_key(VK_CONTROL, key_up=False)
        _send_key(0x56, key_up=False)
        _send_key(0x56, key_up=True)
        _send_key(VK_CONTROL, key_up=True)
        time.sleep(0.15)
        if ole32.OleSetClipboard(original) < 0:
            raise OSError("输入已粘贴，但恢复原剪贴板失败")
        ole32.OleFlushClipboard()
        restored = True
        return True
    finally:
        if not restored:
            try:
                if ole32.OleSetClipboard(original) >= 0:
                    ole32.OleFlushClipboard()
            except Exception:
                pass
        _release_com_pointer(original)
        if initialized in (0, 1):
            ole32.OleUninitialize()


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
        units = send_unicode_text(text, interval)
        
        return parse_result({
            "status": "ok",
            "text_length": len(text),
            "utf16_units": units,
            "method": "send_input"
        })
    except Exception as e:
        return parse_result({"error": str(e)})
