"""Computer use tools - Keyboard operations module.

跨平台支持：Windows/macOS/Linux via pynput。
Windows 上保留剪贴板粘贴回退（OLE 剪贴板恢复）。
"""

import json
import time
from reasonix_computer_use.mcp_server import register_tool
from reasonix_computer_use.utils import parse_result
from reasonix_computer_use.platform_backend import get_keyboard
from reasonix_computer_use.platform_backend import IS_WINDOWS

# Key name validation set (cross-platform)
VALID_KEYS = {
    "enter", "return", "tab", "backspace", "delete", "escape", "esc", "space",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    "insert", "shift", "ctrl", "alt", "win", "meta", "cmd",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
}
VALID_MODIFIERS = {"shift", "ctrl", "alt", "win", "meta", "cmd"}


@register_tool(
    name="computer_keyboard_press",
    description="""按键或组合键。

参数：
- key: 按键名（如 "Enter", "Tab", "Escape", "Space", "F1", "Delete"）
- modifiers: 修饰键列表（如 ["ctrl"], ["ctrl", "shift"]）

按键名（不区分大小写）：
- 导航：Enter, Tab, Backspace, Delete, Escape, Space
- 方向键：Up, Down, Left, Right
- 翻页：Home, End, PageUp, PageDown
- 修饰键：Shift, Ctrl, Alt, Win/Meta
- 功能键：F1-F12

常用组合：
- Ctrl+C: key="c", modifiers=["ctrl"]
- Ctrl+V: key="v", modifiers=["ctrl"]
- Alt+Tab: key="tab", modifiers=["alt"]
""",
    schema={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "按键名（Enter, Tab, Escape, Space, Up, Ctrl 等）"
            },
            "modifiers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "修饰键（ctrl, alt, shift, win）",
                "default": []
            }
        },
        "required": ["key"]
    }
)
async def computer_keyboard_press(args: dict) -> str:
    """Press a key or key combination."""
    key = args.get("key", "").strip()
    modifiers = args.get("modifiers", [])

    try:
        keyboard = get_keyboard()
        keyboard.press_key(key, modifiers)
        time.sleep(0.05)
        return parse_result({"status": "ok", "key": key.lower(), "modifiers": modifiers})
    except Exception as e:
        return parse_result({"error": str(e)})


@register_tool(
    name="computer_keyboard_type",
    description=""""输入 Unicode 文本到焦点控件。

支持中文、日文、特殊字符等任意 Unicode 文本。

参数：
- text: 要输入的文本
- interval: 字符间延迟（秒，默认 0.02）
""",
    schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要输入的文本（支持 Unicode 包括中日韩字符）"
            },
            "interval": {
                "type": "number",
                "default": 0.02,
                "description": "字符间延迟（秒）。默认 0.02s"
            }
        },
        "required": ["text"]
    }
)
async def computer_keyboard_type(args: dict) -> str:
    """Type text character by character."""
    text = args.get("text", "")
    interval = args.get("interval", 0.02)

    try:
        keyboard = get_keyboard()
        keyboard.type_text(text, interval)
        return parse_result({
            "status": "ok",
            "text_length": len(text),
            "method": "pynput"
        })
    except Exception as e:
        return parse_result({"error": str(e)})


def paste_unicode_text(text: str) -> bool:
    """粘贴文本，同时保留原有剪贴板内容（Windows OLE）。

    Windows 上使用 OLE 剪贴板恢复；其他平台使用 pynput 的 Ctrl+V。
    """
    if IS_WINDOWS:
        return _paste_windows(text)
    return _paste_cross_platform(text)


def _paste_windows(text: str) -> bool:
    """Windows: OLE 剪贴板恢复粘贴。"""
    import ctypes
    import ctypes.wintypes
    from ctypes import wintypes

    VK_CONTROL = 0x11
    VK_V = 0x56

    def _send_key(vk_code: int, key_up: bool = False):
        flags = 0x0002 if key_up else 0
        ctypes.windll.user32.keybd_event(vk_code, 0, flags, 0)

    def _set_clipboard_text(text: str) -> None:
        import ctypes
        import ctypes.wintypes
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
        handle = kernel32.GlobalAlloc(0x0002, len(encoded))
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
        for _ in range(20):
            if ctypes.windll.user32.OpenClipboard(None):
                break
            time.sleep(0.01)
        else:
            raise ctypes.WinError()
        try:
            if not ctypes.windll.user32.EmptyClipboard():
                raise ctypes.WinError()
            if not ctypes.windll.user32.SetClipboardData(13, handle):
                raise ctypes.WinError()
            handle = None
        finally:
            ctypes.windll.user32.CloseClipboard()
            if handle:
                kernel32.GlobalFree(handle)

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
        _send_key(VK_V, key_up=False)
        _send_key(VK_V, key_up=True)
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
        if initialized in (0, 1):
            ole32.OleUninitialize()


def _paste_cross_platform(text: str) -> bool:
    """跨平台粘贴：使用 pynput 的 Ctrl+V / Cmd+V。"""
    try:
        import subprocess
        import platform

        # 先保存当前剪贴板（尽力而为）
        current_clip = ""
        try:
            if platform.system().lower() == "darwin":
                import subprocess
                current_clip = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
            elif platform.system().lower() == "linux":
                import subprocess
                current_clip = subprocess.run(["xclip", "-o", "-selection", "clipboard"], capture_output=True, text=True).stdout
        except Exception:
            pass

        # 写入新文本到剪贴板
        try:
            if platform.system().lower() == "darwin":
                subprocess.run(["pbcopy"], input=text, text=True, check=True)
            elif platform.system().lower() == "linux":
                subprocess.run(["xclip", "-i", "-selection", "clipboard"], input=text, text=True, check=True)
        except Exception:
            pass

        # 发送 Ctrl+V / Cmd+V
        keyboard = get_keyboard()
        modifier = "cmd" if platform.system().lower() == "darwin" else "ctrl"
        keyboard.press_key("v", [modifier])
        time.sleep(0.15)

        # 恢复原始剪贴板
        try:
            if platform.system().lower() == "darwin":
                subprocess.run(["pbcopy"], input=current_clip, text=True, check=True)
            elif platform.system().lower() == "linux":
                subprocess.run(["xclip", "-i", "-selection", "clipboard"], input=current_clip, text=True, check=True)
        except Exception:
            pass

        return True
    except Exception:
        return False
