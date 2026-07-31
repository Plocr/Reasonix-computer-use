"""
Linux PlatformProvider — X11 implementation using python-xlib (XTEST).

All coordinates accepted by public methods are PHYSICAL screen pixels.
Mouse/keyboard injection uses the XTEST extension (works under X11 and
XWayland); EWMH hints drive window enumeration/activation.

Wayland sessions (native, non-XWayland) cannot be driven with global
coordinates or input injection — methods that need them raise
NotImplementedError, and the perception layer falls back to vision.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .base import PlatformProvider, WindowInfo
from .common import (
    CLICK_GAP,
    FfmpegRecorder,
    MAX_DURATION,
    MOVE_SETTLE,
    PRESS_HOLD,
    SCROLL_SETTLE,
    clamp_count,
    drag_plan,
    normalize_key_name,
)

# ── X11 button numbers (X11 convention: 1=left, 2=middle, 3=right) ────────
_BUTTON_NUMBERS = {"left": 1, "middle": 2, "right": 3}

# Scroll wheel buttons: 4=up, 5=down, 6=left, 7=right.
# Keyed by (direction, delta_positive): a positive wheel delta means
# up/right, negative means down/left (mirrors the Windows semantics where
# amount>0 + direction in ("up","right") yields a positive delta).
_SCROLL_BUTTONS = {
    ("vertical", True): 4,    # up
    ("vertical", False): 5,   # down
    ("horizontal", True): 6,  # left
    ("horizontal", False): 7,  # right
}

# Characters that live on the shifted layer of the keyboard.
_SHIFT_LAYER = set("!@#$%^&*()_+{}|:\"<>?~")

# Key names → X11 keysyms.  Modifier aliases are normalized by common.
_KEYSYM_MAP = {
    "enter": "Return", "return": "Return", "tab": "Tab",
    "backspace": "BackSpace", "delete": "Delete",
    "escape": "Escape", "esc": "Escape", "space": "space",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Page_Up", "pagedown": "Page_Down",
    "insert": "Insert", "pause": "Pause", "capslock": "Caps_Lock",
    "printscreen": "Print", "prtsc": "Print", "apps": "Menu",
    "numlock": "Num_Lock", "scrolllock": "Scroll_Lock",
    "shift": "Shift_L", "ctrl": "Control_L", "alt": "Alt_L",
    "super": "Super_L", "meta": "Super_L", "option": "Alt_R",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
    "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
    "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
    "volume_mute": "XF86AudioMute", "volume_down": "XF86AudioLowerVolume",
    "volume_up": "XF86AudioRaiseVolume", "media_next": "XF86AudioNext",
    "media_prev": "XF86AudioPrev", "media_stop": "XF86AudioStop",
    "media_play_pause": "XF86AudioPlay",
}


class LinuxPlatformProvider(PlatformProvider):
    """Full X11 implementation backed by python-xlib and the XTEST extension."""

    def __init__(self):
        self._display = None
        self._recorder = FfmpegRecorder()
        self._session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()

    # ── Session / display helpers ─────────────────────────────────────────

    @property
    def is_wayland(self) -> bool:
        return self._session_type == "wayland"

    def _ensure_display(self):
        if self._display is None:
            from Xlib import display as xdisplay
            self._display = xdisplay.Display()
        return self._display

    def _require_x11(self, method: str):
        if self.is_wayland:
            raise NotImplementedError(
                f"{method} is unavailable under a native Wayland session "
                "(no global coordinate / input injection APIs). Run under "
                "X11 or XWayland, or use the vision fallback.")

    def _keysym(self, name: str) -> int:
        from Xlib import XK
        canonical = _KEYSYM_MAP.get(normalize_key_name(name), "")
        if canonical:
            return XK.string_to_keysym(canonical)
        return XK.string_to_keysym(name)

    # ── Mouse ────────────────────────────────────────────────────────────

    def mouse_move(self, x: int, y: int) -> None:
        x, y = int(x), int(y)
        self._require_x11("mouse_move")
        from Xlib import X
        from Xlib.ext import xtest
        d = self._ensure_display()
        root = d.screen().root
        xtest.fake_input(d, X.MotionNotify, x=x, y=y)
        d.sync()
        # Verify (XQueryPointer) — mirrors the Windows coordinate check
        actual = root.query_pointer()
        if (actual.root_x, actual.root_y) != (x, y):
            raise OSError(
                f"Mouse physical coordinate mismatch: requested=({x},{y}), "
                f"actual=({actual.root_x},{actual.root_y})")

    def mouse_click(self, x: int, y: int, button: str = "left",
                    count: int = 1, duration: float = 0.0) -> None:
        if button not in _BUTTON_NUMBERS:
            raise ValueError(f"Unknown button: {button}")
        self._require_x11("mouse_click")
        from Xlib import X
        from Xlib.ext import xtest
        d = self._ensure_display()
        number = _BUTTON_NUMBERS[button]
        count = clamp_count(count)

        self.mouse_move(x, y)
        time.sleep(MOVE_SETTLE)

        # Same human-like rhythm as Windows (single source in common).
        for _ in range(count):
            xtest.fake_input(d, X.ButtonPress, number)
            time.sleep(PRESS_HOLD)
            xtest.fake_input(d, X.ButtonRelease, number)
            if _ < count - 1:
                time.sleep(CLICK_GAP)
        d.sync()

        if duration:
            time.sleep(min(duration, MAX_DURATION))

    def mouse_drag(self, from_x: int, from_y: int,
                   to_x: int, to_y: int, duration: float = 0.5) -> None:
        self._require_x11("mouse_drag")
        from Xlib import X
        from Xlib.ext import xtest
        d = self._ensure_display()
        points, delay = drag_plan((from_x, from_y), (to_x, to_y), duration)

        self.mouse_move(from_x, from_y)
        xtest.fake_input(d, X.ButtonPress, 1)
        time.sleep(MOVE_SETTLE)
        try:
            for cx, cy in points:
                xtest.fake_input(d, X.MotionNotify, x=cx, y=cy)
                d.sync()
                time.sleep(delay)
        finally:
            xtest.fake_input(d, X.ButtonRelease, 1)
            d.sync()

    def mouse_scroll(self, x: int, y: int, amount: int,
                     direction: str = "vertical") -> None:
        self._require_x11("mouse_scroll")
        from Xlib import X
        from Xlib.ext import xtest
        d = self._ensure_display()
        self.mouse_move(x, y)
        time.sleep(MOVE_SETTLE)

        positive = direction in ("up", "right")
        delta = amount * 120 if positive else -amount * 120
        button = _SCROLL_BUTTONS.get((direction, delta > 0))
        if button is None:
            raise ValueError(f"Unknown scroll direction: {direction}")
        for _ in range(max(1, abs(int(amount)))):
            xtest.fake_input(d, X.ButtonPress, button)
            xtest.fake_input(d, X.ButtonRelease, button)
            time.sleep(SCROLL_SETTLE)
        d.sync()

    # ── Keyboard ─────────────────────────────────────────────────────────

    def _resolve_keycode(self, name: str) -> int:
        """Resolve a key name to an X11 keycode (0 when unknown)."""
        d = self._ensure_display()
        keysym = self._keysym(name)
        if keysym == 0:
            return 0
        return d.keysym_to_keycode(keysym)

    def keyboard_type(self, text: str) -> None:
        """Type Unicode text.

        Printable ASCII is injected via XTEST (with shift for uppercase /
        shifted symbols).  Non-ASCII text (CJK, emoji, ...) is pasted via
        the clipboard (xclip/xsel) with the previous clipboard restored.
        """
        if not text:
            return
        self._require_x11("keyboard_type")
        if all(self._type_ascii_char(ch) for ch in text):
            return
        self._paste_via_clipboard(text)

    def _type_ascii_char(self, ch: str) -> bool:
        """Inject one printable ASCII char via XTEST.  False = not injectable."""
        from Xlib import X, XK
        from Xlib.ext import xtest
        d = self._ensure_display()
        code = ord(ch)
        if code > 0x7E:
            return False
        if ch in _SHIFT_LAYER or ch.isupper():
            # Shifted layer — hold Shift_L while pressing the base key.
            # The keysym of the shifted char maps to the base keycode.
            keycode = d.keysym_to_keycode(code)
            if not keycode:
                return False
            shift = d.keysym_to_keycode(XK.string_to_keysym("Shift_L"))
            if not shift:
                return False
            xtest.fake_input(d, X.KeyPress, shift)
            xtest.fake_input(d, X.KeyPress, keycode)
            xtest.fake_input(d, X.KeyRelease, keycode)
            xtest.fake_input(d, X.KeyRelease, shift)
        else:
            keycode = d.keysym_to_keycode(code)
            if not keycode:
                return False
            xtest.fake_input(d, X.KeyPress, keycode)
            xtest.fake_input(d, X.KeyRelease, keycode)
        d.sync()
        return True

    def _paste_via_clipboard(self, text: str) -> None:
        """Paste text through the clipboard, restoring the previous content."""
        clipboard = self._read_clipboard()
        tool = self._clipboard_tool()
        if tool is None:
            raise OSError(
                "Non-ASCII text needs a clipboard tool (xclip or xsel) on "
                "Linux; install one of them to type CJK/emoji text")
        try:
            self._write_clipboard(tool, text)
            ctrl = self._resolve_keycode("ctrl")
            v = self._resolve_keycode("v")
            from Xlib import X
            from Xlib.ext import xtest
            d = self._ensure_display()
            xtest.fake_input(d, X.KeyPress, ctrl)
            xtest.fake_input(d, X.KeyPress, v)
            xtest.fake_input(d, X.KeyRelease, v)
            xtest.fake_input(d, X.KeyRelease, ctrl)
            d.sync()
            time.sleep(0.05)
        finally:
            if clipboard is not None:
                self._write_clipboard(tool, clipboard)

    @staticmethod
    def _clipboard_tool() -> Optional[str]:
        for tool in ("xclip", "xsel"):
            if LinuxPlatformProvider._which(tool):
                return tool
        return None

    @staticmethod
    def _which(name: str) -> Optional[str]:
        from shutil import which
        return which(name)

    def _read_clipboard(self) -> Optional[str]:
        tool = self._clipboard_tool()
        if tool is None:
            return None
        try:
            args = [tool, "-o"] if tool == "xclip" else [tool, "--output"]
            result = subprocess.run(args, capture_output=True, text=True,
                                    timeout=3)
            return result.stdout if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def _write_clipboard(self, tool: str, text: str) -> None:
        args = [tool, "-selection", "clipboard", "-i"] if tool == "xclip" \
            else [tool, "--input", "--clipboard"]
        subprocess.run(args, input=text, capture_output=True, timeout=3,
                       check=False)

    def keyboard_press(self, keys: List[str]) -> None:
        if not keys:
            return
        self._require_x11("keyboard_press")
        from Xlib import X
        from Xlib.ext import xtest
        d = self._ensure_display()

        *modifiers, final = keys
        mod_keycodes = [self._resolve_keycode(m) for m in modifiers]
        final_keycode = self._resolve_keycode(final)
        if not final_keycode:
            raise ValueError(f"Unknown key: {final}")
        if any(kc == 0 for kc in mod_keycodes):
            raise ValueError(f"Unknown modifier key: {modifiers}")

        all_keycodes = mod_keycodes + [final_keycode]
        for kc in all_keycodes:
            xtest.fake_input(d, X.KeyPress, kc)
        for kc in reversed(all_keycodes):
            xtest.fake_input(d, X.KeyRelease, kc)
        d.sync()

    def keyboard_key_down(self, key: str) -> None:
        self._require_x11("keyboard_key_down")
        from Xlib import X
        from Xlib.ext import xtest
        d = self._ensure_display()
        keycode = self._resolve_keycode(key)
        if not keycode:
            raise ValueError(f"Unknown key: {key}")
        xtest.fake_input(d, X.KeyPress, keycode)
        d.sync()

    def keyboard_key_up(self, key: str) -> None:
        self._require_x11("keyboard_key_up")
        from Xlib import X
        from Xlib.ext import xtest
        d = self._ensure_display()
        keycode = self._resolve_keycode(key)
        if not keycode:
            raise ValueError(f"Unknown key: {key}")
        xtest.fake_input(d, X.KeyRelease, keycode)
        d.sync()

    # ── Screen ───────────────────────────────────────────────────────────

    def screenshot(self, rect: Optional[Tuple[int, int, int, int]] = None):
        from PIL import Image
        if self.is_wayland:
            return self._screenshot_wayland(rect)
        try:
            import mss
        except ImportError:
            return self._screenshot_xlib(rect)
        with mss.mss() as sct:
            if rect is None:
                monitor = sct.monitors[0]  # virtual screen
            else:
                left, top, right, bottom = rect
                monitor = {"left": left, "top": top,
                           "width": right - left, "height": bottom - top}
            shot = sct.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.rgb)

    def _screenshot_xlib(self, rect):
        """Fallback screenshot via XGetImage (no mss installed)."""
        self._require_x11("screenshot")
        from PIL import Image
        from Xlib import X
        d = self._ensure_display()
        root = d.screen().root
        if rect is None:
            rect = self.get_virtual_screen_rect()
        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        raw = root.get_image(left, top, width, height, X.ZPixmap, 0xFFFFFFFF)
        data = raw.data
        if raw.bits_per_pixel == 32:
            # X11 32bpp is BGRA in memory
            img = Image.frombytes("RGBA", (width, height), data, "raw",
                                  "BGRA").convert("RGB")
        else:
            img = Image.frombytes("RGB", (width, height), data)
        return img

    def _screenshot_wayland(self, rect):
        """Wayland screenshot via the grim external tool (best-effort)."""
        from PIL import Image
        if rect is None:
            geom = ""
        else:
            left, top, right, bottom = rect
            geom = f"{right - left}x{bottom - top}+{left}+{top}"
        args = ["grim"]
        if geom:
            args.append("-g")
            args.append(geom)
        result = subprocess.run(args, capture_output=True, timeout=5)
        if result.returncode != 0:
            raise OSError(
                "Wayland screenshot requires the 'grim' tool "
                f"(exit {result.returncode})")
        import io
        return Image.open(io.BytesIO(result.stdout)).convert("RGB")

    def get_virtual_screen_rect(self) -> Tuple[int, int, int, int]:
        if self.is_wayland:
            raise NotImplementedError(
                "get_virtual_screen_rect is unavailable under Wayland")
        try:
            import mss
            with mss.mss() as sct:
                mon = sct.monitors[0]
            return (mon["left"], mon["top"],
                    mon["left"] + mon["width"], mon["top"] + mon["height"])
        except ImportError:
            self._require_x11("get_virtual_screen_rect")
            d = self._ensure_display()
            root = d.screen().root
            geom = root.get_geometry()
            return (0, 0, geom.width, geom.height)

    # ── Window (EWMH) ────────────────────────────────────────────────────

    def _root_window(self):
        d = self._ensure_display()
        return d.screen().root

    def _ewmh_property(self, atom_name: str):
        """Return the list of window ids for an _NET_* root property."""
        from Xlib import X
        d = self._ensure_display()
        root = self._root_window()
        atom = d.intern_atom(atom_name)
        try:
            value = root.get_full_property(atom, X.AnyPropertyType)
        except Exception:
            return None
        if value is None:
            return None
        # 32-bit property → list of window ids (Xlib returns array.array)
        return list(value.value)

    def _window_title(self, window) -> str:
        d = self._ensure_display()
        for atom_name in ("_NET_WM_NAME", "WM_NAME"):
            atom = d.intern_atom(atom_name)
            try:
                prop = window.get_full_property(atom, 0)
            except Exception:
                continue
            if prop is not None:
                value = prop.value
                if isinstance(value, bytes):
                    return value.decode("utf-8", "replace")
                if isinstance(value, str):
                    return value
        return ""

    def _window_rect(self, window) -> Tuple[int, int, int, int]:
        d = self._ensure_display()
        root = self._root_window()
        geom = window.get_geometry()
        try:
            translated = window.translate_coords(root, 0, 0)
            left, top = translated.x, translated.y
        except Exception:
            left, top = 0, 0
        return (left, top, left + geom.width, top + geom.height)

    def _window_pid(self, window) -> int:
        d = self._ensure_display()
        atom = d.intern_atom("_NET_WM_PID")
        try:
            prop = window.get_full_property(atom, 0)
            if prop is not None and prop.value:
                return int(prop.value[0])
        except Exception:
            pass
        return 0

    @staticmethod
    def _process_name(pid: int) -> str:
        if pid <= 0:
            return ""
        try:
            comm = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
            return comm[:255]
        except OSError:
            return ""

    def _scale_factor(self) -> float:
        try:
            dpi_value = self._ensure_display().get_default(
                self._root_window(), "Xft", "dpi")
            if dpi_value:
                return max(1.0, float(dpi_value) / 96.0)
        except Exception:
            pass
        return 1.0

    def _build_window_info(self, window) -> Optional[WindowInfo]:
        try:
            title = self._window_title(window)
            if not title:
                return None
            rect = self._window_rect(window)
            if rect[2] - rect[0] < 10 or rect[3] - rect[1] < 10:
                return None
            pid = self._window_pid(window)
            scale = self._scale_factor()
            return WindowInfo(
                id=str(window.id),
                title=title,
                process_name=self._process_name(pid),
                process_id=pid,
                rect=rect,
                dpi=int(96 * scale),
                scale_factor=scale,
            )
        except Exception:
            return None

    def list_windows(self) -> List[WindowInfo]:
        self._require_x11("list_windows")
        d = self._ensure_display()
        root = self._root_window()
        results: List[WindowInfo] = []
        client_ids = self._ewmh_property("_NET_CLIENT_LIST")
        if not client_ids:
            return results
        for wid in client_ids:
            window = d.create_resource_object("window", wid)
            # Skip windows in the _NET_WM_STATE_HIDDEN state
            state_atom = d.intern_atom("_NET_WM_STATE")
            hidden_atom = d.intern_atom("_NET_WM_STATE_HIDDEN")
            try:
                state = window.get_full_property(state_atom, 0)
                if state is not None and hidden_atom in list(state.value):
                    continue
            except Exception:
                pass
            info = self._build_window_info(window)
            if info is not None:
                results.append(info)
        return results

    def get_window_rect(self, window_id: str) -> Tuple[int, int, int, int]:
        self._require_x11("get_window_rect")
        d = self._ensure_display()
        window = d.create_resource_object("window", int(window_id))
        return self._window_rect(window)

    def activate_window(self, window_id: str) -> bool:
        self._require_x11("activate_window")
        from Xlib import X, Xutil
        d = self._ensure_display()
        root = self._root_window()
        window = d.create_resource_object("window", int(window_id))

        # 1. EWMH: send _NET_ACTIVE_WINDOW client message (compositor-driven)
        active_atom = d.intern_atom("_NET_ACTIVE_WINDOW")
        event = X.ClientMessageEvent(
            window=window, client_type=active_atom, data=(32, [1, X.CurrentTime, 0, 0, 0]))
        root.send_event(event, event_mask=X.SubstructureRedirectMask
                        | X.SubstructureNotifyMask)
        # 2. Fallbacks: raise + set input focus
        try:
            window.raise_window()
            window.set_input_focus(X.RevertToParent, X.CurrentTime)
        except Exception:
            pass
        d.sync()
        time.sleep(0.05)
        return self.get_foreground_window() is not None and \
            self.get_foreground_window().id == str(window_id)

    def get_foreground_window(self) -> Optional[WindowInfo]:
        if self.is_wayland:
            return None
        d = self._ensure_display()
        active = self._ewmh_property("_NET_ACTIVE_WINDOW")
        if not active:
            return None
        window = d.create_resource_object("window", int(active[0]))
        return self._build_window_info(window)

    # ── Recording ────────────────────────────────────────────────────────

    def start_recording(self, output_path: Path,
                        rect: Optional[Tuple[int, int, int, int]] = None) -> bool:
        """Record the screen via FFmpeg x11grab (X11 capture)."""

        def args_builder(capture_rect):
            display_name = os.environ.get("DISPLAY", ":0")
            args = [
                "ffmpeg", "-y",
                "-f", "x11grab",
                "-framerate", "15",
            ]
            if capture_rect:
                left, top, right, bottom = capture_rect
                args += ["-video_size", f"{right - left}x{bottom - top}"]
                args += ["-i", f"{display_name}+{left},{top}"]
            else:
                args += ["-i", display_name]
            args += [str(output_path)]
            return args

        return self._recorder.start(output_path, args_builder, rect)

    def stop_recording(self) -> Optional[Path]:
        return self._recorder.stop()
