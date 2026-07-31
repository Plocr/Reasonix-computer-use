"""Tests for the Linux (X11) platform provider.

The real X11 stack is not available on the dev machine, so these tests
inject a fake ``Xlib`` package into ``sys.modules`` and verify behaviour
contracts: Wayland restrictions, keysym resolution, human-like click
timing, XTEST event sequences, clipboard fallback and ffmpeg args.
Real X11 behaviour is exercised in CI via xvfb-run and on real machines.
"""

from __future__ import annotations

import os
import sys
import types
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace

import pytest


# ── Fake Xlib (installed into sys.modules before importing linux.py) ─────

def _install_fake_xlib():
    Xlib = types.ModuleType("Xlib")
    Xlib.ext = types.ModuleType("Xlib.ext")
    Xlib.ext.xtest = types.ModuleType("Xlib.ext.xtest")
    Xlib.X = types.ModuleType("Xlib.X")
    Xlib.XK = types.ModuleType("Xlib.XK")
    Xlib.display = types.ModuleType("Xlib.display")

    calls: list[dict] = []

    def fake_input(display, event_type, detail=0, st=0, root=None, x=0, y=0):
        calls.append({"type": event_type, "detail": detail, "x": x, "y": y})

    Xlib.ext.xtest.fake_input = fake_input

    Xlib.X.MotionNotify = 6
    Xlib.X.ButtonPress = 4
    Xlib.X.ButtonRelease = 5
    Xlib.X.KeyPress = 2
    Xlib.X.KeyRelease = 3
    Xlib.X.CurrentTime = 0
    Xlib.X.AnyPropertyType = 0
    Xlib.X.SubstructureRedirectMask = 1 << 20
    Xlib.X.SubstructureNotifyMask = 1 << 19
    Xlib.X.RevertToParent = 2
    Xlib.X.ZPixmap = 2
    Xlib.X.ClientMessageEvent = mock.MagicMock()

    def string_to_keysym(name: str) -> int:
        # Deterministic fake: single chars resolve to their code point
        # (like the real keysym table); multi-char names get a weighted
        # sum so distinct names never collide.
        if len(name) == 1:
            return ord(name)
        return 0x1000 + (sum(ord(c) * (i + 1) for i, c in enumerate(name)) % 0xFFFF)

    Xlib.XK.string_to_keysym = string_to_keysym

    class _FakeWindow:
        def __init__(self, disp, wid):
            self.id = wid
            self._d = disp

        def get_geometry(self):
            return SimpleNamespace(width=800, height=600)

        def translate_coords(self, root, x, y):
            return SimpleNamespace(x=0, y=0)

        def get_full_property(self, atom, prop_type):
            if atom == "_NET_WM_NAME":
                return SimpleNamespace(value=b"My Window")
            if atom == "_NET_WM_PID":
                return SimpleNamespace(value=[1234])
            return None

        def raise_window(self):
            pass

        def set_input_focus(self, *args):
            pass

    class _FakeRoot:
        def __init__(self, disp):
            self._d = disp

        def query_pointer(self):
            return SimpleNamespace(root_x=100, root_y=100)

        def get_geometry(self):
            return SimpleNamespace(width=1920, height=1080)

        def get_full_property(self, atom, prop_type):
            if atom == "_NET_CLIENT_LIST":
                return SimpleNamespace(value=[10, 20])
            return None

        def send_event(self, *args, **kwargs):
            pass

    class _FakeDisplay:
        def __init__(self):
            # keysym -> keycode
            self._keycodes = {
                ord("H"): 38, ord("i"): 43, ord("a"): 38,
                string_to_keysym("Shift_L"): 50,
                string_to_keysym("Control_L"): 37,
                string_to_keysym("Super_L"): 133,
            }

        def keysym_to_keycode(self, keysym: int) -> int:
            return self._keycodes.get(keysym, 0)

        def sync(self):
            pass

        def screen(self):
            return SimpleNamespace(root=_FakeRoot(self))

        def intern_atom(self, name):
            return name

        def create_resource_object(self, kind, wid):
            return _FakeWindow(self, wid)

        def get_default(self, window, cls, res):
            return None

    Xlib.display.Display = _FakeDisplay

    sys.modules["Xlib"] = Xlib
    sys.modules["Xlib.ext"] = Xlib.ext
    sys.modules["Xlib.ext.xtest"] = Xlib.ext.xtest
    sys.modules["Xlib.X"] = Xlib.X
    sys.modules["Xlib.XK"] = Xlib.XK
    sys.modules["Xlib.display"] = Xlib.display
    return Xlib, calls


@pytest.fixture
def xlib_env(monkeypatch):
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":99")
    return _install_fake_xlib()


@pytest.fixture
def provider(xlib_env):
    from reasonix_computer_use.platform.linux import LinuxPlatformProvider
    p = LinuxPlatformProvider()
    p._display = xlib_env[0].display.Display()
    return p


# ── Wayland restrictions ─────────────────────────────────────────────────

def test_wayland_restricts_global_operations(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    from reasonix_computer_use.platform.linux import LinuxPlatformProvider
    p = LinuxPlatformProvider()
    assert p.is_wayland
    with pytest.raises(NotImplementedError):
        p.mouse_move(10, 10)
    with pytest.raises(NotImplementedError):
        p.keyboard_type("hi")
    with pytest.raises(NotImplementedError):
        p.keyboard_press(["ctrl", "c"])
    with pytest.raises(NotImplementedError):
        p.list_windows()
    with pytest.raises(NotImplementedError):
        p.activate_window("1")


def test_x11_session_not_wayland(monkeypatch, provider):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert not provider.is_wayland


# ── Keysym resolution ────────────────────────────────────────────────────

def test_keysym_mapping_and_normalization(provider, xlib_env):
    xk = xlib_env[0].XK
    # "win"/"cmd"/"meta" normalize to Super_L before keysym lookup
    assert provider._keysym("win") == xk.string_to_keysym("Super_L")
    assert provider._keysym("cmd") == xk.string_to_keysym("Super_L")
    assert provider._keysym("ctrl") == xk.string_to_keysym("Control_L")
    assert provider._keysym("enter") == xk.string_to_keysym("Return")


# ── Mouse ────────────────────────────────────────────────────────────────

def test_mouse_click_human_timing(provider, xlib_env, monkeypatch):
    _, calls = xlib_env
    sleeps: list[float] = []
    monkeypatch.setattr("reasonix_computer_use.platform.linux.time.sleep",
                        lambda s: sleeps.append(s))
    provider.mouse_move = mock.Mock()  # avoid the XTEST move path

    provider.mouse_click(100, 100, count=2)

    buttons = [c["detail"] for c in calls if c["type"] in (4, 5)]
    assert buttons == [1, 1, 1, 1]  # press+release twice on button 1
    assert sleeps == [0.03, 0.06, 0.20, 0.06]  # MOVE_SETTLE, PRESS_HOLD, CLICK_GAP, PRESS_HOLD


def test_mouse_click_unknown_button(provider):
    with pytest.raises(ValueError):
        provider.mouse_click(0, 0, button="side")


def test_mouse_scroll_buttons(provider, xlib_env, monkeypatch):
    _, calls = xlib_env
    monkeypatch.setattr("reasonix_computer_use.platform.linux.time.sleep",
                        lambda s: None)
    provider.mouse_move = mock.Mock()
    provider.mouse_scroll(10, 10, amount=2)          # down -> button 5
    provider.mouse_scroll(10, 10, amount=-1, direction="vertical")  # up -> button 4
    buttons = [c["detail"] for c in calls if c["type"] == 4]
    assert buttons == [5, 5, 4]

    # horizontal: amount>0 = right -> button 7; amount<0 = left -> button 6
    calls.clear()
    provider.mouse_scroll(10, 10, amount=1, direction="horizontal")
    provider.mouse_scroll(10, 10, amount=-1, direction="horizontal")
    assert [c["detail"] for c in calls if c["type"] == 4] == [7, 6]


# ── Keyboard ─────────────────────────────────────────────────────────────

def test_keyboard_type_ascii_with_shift(provider, xlib_env, monkeypatch):
    _, calls = xlib_env
    monkeypatch.setattr("reasonix_computer_use.platform.linux.time.sleep",
                        lambda s: None)
    provider.keyboard_type("Hi")
    events = [(c["type"], c["detail"]) for c in calls]
    assert events == [
        (2, 50),   # shift down
        (2, 38),   # 'H' press
        (3, 38),   # 'H' release
        (3, 50),   # shift up
        (2, 43),   # 'i' press
        (3, 43),   # 'i' release
    ]


def test_keyboard_type_non_ascii_uses_clipboard(provider, xlib_env, monkeypatch):
    _, calls = xlib_env
    monkeypatch.setattr("reasonix_computer_use.platform.linux.time.sleep",
                        lambda s: None)
    monkeypatch.setattr(
        "reasonix_computer_use.platform.linux.LinuxPlatformProvider._which",
        staticmethod(lambda name: f"/usr/bin/{name}" if name == "xclip" else None))
    run = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout="old"))
    monkeypatch.setattr("reasonix_computer_use.platform.linux.subprocess.run", run)

    provider.keyboard_type("你好")

    # old clipboard read once, new text written, ctrl+v injected, old restored
    assert run.call_count == 3
    tools = [c.args[0][0] for c in run.call_args_list]
    assert tools == ["xclip", "xclip", "xclip"]
    pasted = [c.kwargs.get("input") for c in run.call_args_list]
    assert pasted[0] is None       # read
    assert pasted[1] == "你好"      # write new
    assert pasted[2] == "old"       # restore
    keys = [(c["type"], c["detail"]) for c in calls]
    assert keys == [(2, 37), (2, 0), (3, 0), (3, 37)]  # ctrl+v down/up pair


def test_keyboard_press_combination(provider, xlib_env, monkeypatch):
    _, calls = xlib_env
    monkeypatch.setattr("reasonix_computer_use.platform.linux.time.sleep",
                        lambda s: None)
    provider.keyboard_press(["ctrl", "a"])
    events = [(c["type"], c["detail"]) for c in calls]
    assert events == [
        (2, 37),   # ctrl down
        (2, 38),   # a down
        (3, 38),   # a up
        (3, 37),   # ctrl up
    ]


def test_keyboard_press_unknown_key(provider):
    with pytest.raises(ValueError):
        provider.keyboard_press(["not_a_key"])


# ── Windows / EWMH ───────────────────────────────────────────────────────

def test_list_windows_ewmh(provider):
    windows = provider.list_windows()
    assert len(windows) == 2
    win = windows[0]
    assert win.title == "My Window"
    assert win.process_id == 1234
    assert win.rect == (0, 0, 800, 600)
    assert win.scale_factor == 1.0


def test_foreground_window_returns_none_on_wayland(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    from reasonix_computer_use.platform.linux import LinuxPlatformProvider
    assert LinuxPlatformProvider().get_foreground_window() is None


# ── Recording ────────────────────────────────────────────────────────────

def test_start_recording_builds_x11grab_args(provider, xlib_env, monkeypatch):
    rec = mock.Mock()
    provider._recorder = rec
    provider.start_recording(Path("/tmp/o.mp4"), (0, 0, 1920, 1080))
    args_builder = rec.start.call_args.args[1]
    args = args_builder((0, 0, 1920, 1080))
    assert "x11grab" in args
    assert "-video_size" in args and "1920x1080" in args
    assert ":99+0,0" in args
    assert str(Path("/tmp/o.mp4")) in args
