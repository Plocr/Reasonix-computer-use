"""Tests for the macOS platform provider.

The PyObjC stack is unavailable on the dev machine, so a fake
``Quartz``/``AppKit``/``ApplicationServices`` is injected into
``sys.modules``.  Verified contracts: permission gating, keycode mapping,
human-like click timing, Unicode keyboard injection, window conversion
with scale factor, and avfoundation recording args.
Real behaviour runs on macOS CI and real machines (needs Accessibility).
"""

from __future__ import annotations

import sys
import types
import unittest.mock as mock
from pathlib import Path

import pytest


def _install_fake_pyobjc():
    Quartz = types.ModuleType("Quartz")
    AppKit = types.ModuleType("AppKit")
    ApplicationServices = types.ModuleType("ApplicationServices")

    # kVK_* constants (any distinct ints)
    vk_names = ["kVK_Return", "kVK_Tab", "kVK_Delete", "kVK_ForwardDelete",
                "kVK_Escape", "kVK_Space", "kVK_UpArrow", "kVK_DownArrow",
                "kVK_LeftArrow", "kVK_RightArrow", "kVK_Home", "kVK_End",
                "kVK_PageUp", "kVK_PageDown", "kVK_Help", "kVK_CapsLock",
                "kVK_F13", "kVK_F14", "kVK_F15", "kVK_F16", "kVK_Clear",
                "kVK_Shift", "kVK_Control", "kVK_Option", "kVK_Command",
                "kVK_F1", "kVK_F2", "kVK_F3", "kVK_F4", "kVK_F5", "kVK_F6",
                "kVK_F7", "kVK_F8", "kVK_F9", "kVK_F10", "kVK_F11", "kVK_F12",
                "kVK_Mute", "kVK_VolumeDown", "kVK_VolumeUp", "kVK_Next",
                "kVK_Previous", "kVK_Play"]
    for i, name in enumerate(vk_names, start=1):
        setattr(Quartz, name, i)

    Quartz.kCGHIDEventTap = 1
    Quartz.kCGEventLeftMouseDown = 11
    Quartz.kCGEventLeftMouseUp = 12
    Quartz.kCGEventRightMouseDown = 13
    Quartz.kCGEventRightMouseUp = 14
    Quartz.kCGEventOtherMouseDown = 15
    Quartz.kCGEventOtherMouseUp = 16
    Quartz.kCGEventMouseMoved = 17
    Quartz.kCGEventLeftMouseDragged = 18
    Quartz.kCGEventFlagMaskShift = 1 << 17
    Quartz.kCGEventFlagMaskControl = 1 << 18
    Quartz.kCGEventFlagMaskAlternate = 1 << 19
    Quartz.kCGEventFlagMaskCommand = 1 << 20
    Quartz.kCGScrollWheelEventDeltaAxis1 = 11
    Quartz.kCGScrollWheelEventDeltaAxis2 = 12
    Quartz.kCGScrollEventUnitLine = 0
    Quartz.kCGWindowListOptionOnScreenOnly = 1
    Quartz.kCGWindowListExcludeDesktopElements = 16
    Quartz.kCGNullWindowID = 0
    Quartz.CGRectNull = None

    class _FakeCType:
        """Mimic a PyObjC ctypes type: ``Type * n`` yields a callable
        returning an indexable array (used by CGGetActiveDisplayList)."""

        def __mul__(self, n):
            return lambda: [0] * n

    Quartz.CGDirectDisplayID = _FakeCType()

    calls: list[dict] = []

    def CGEventCreateMouseEvent(source, mouse_type, pos, button):
        calls.append({"kind": "mouse", "type": mouse_type,
                      "pos": tuple(pos), "button": button})
        return object()

    def CGEventPost(tap, event):
        calls.append({"kind": "post", "tap": tap, "event": event})

    def CGEventCreateKeyboardEvent(source, keycode, key_down):
        ev = {"keycode": keycode, "down": key_down, "flags": 0, "text": None}
        calls.append({"kind": "key", "event": ev})
        return ev

    def CGEventSetFlags(event, flags):
        event["flags"] = flags

    def CGEventKeyboardSetUnicodeString(event, length, text):
        event["text"] = str(text)

    def CGEventCreateScrollWheelEvent(source, unit, wheel_count, delta):
        ev = {"delta": delta}
        calls.append({"kind": "scroll", "event": ev})
        return ev

    def CGEventSetIntegerValueField(event, field, value):
        event["field"] = field
        event["value"] = value

    def CGWindowListCopyWindowInfo(option, relative):
        return [{
            "kCGWindowNumber": 101,
            "kCGWindowName": "Test Window",
            "kCGWindowOwnerPID": 501,
            "kCGWindowOwnerName": "TestApp",
            "kCGWindowLayer": 0,
            "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 800, "Height": 600},
        }, {
            "kCGWindowNumber": 102,
            "kCGWindowName": "",
            "kCGWindowOwnerPID": 502,
            "kCGWindowOwnerName": "Other",
            "kCGWindowLayer": 0,
            "kCGWindowBounds": {"X": 100, "Y": 100, "Width": 50, "Height": 50},
        }]

    def CGGetActiveDisplayList(max_disp, ids, count):
        ids[0] = 1
        return 1

    def CGDisplayBounds(display_id):
        return type("Rect", (), {"origin": type("O", (), {"x": 0, "y": 0})(),
                                 "size": type("S", (), {"width": 1440, "height": 900})()})()

    def CGRectUnion(a, b, c):
        return a

    Quartz.CGEventCreateMouseEvent = CGEventCreateMouseEvent
    Quartz.CGEventPost = CGEventPost
    Quartz.CGEventCreateKeyboardEvent = CGEventCreateKeyboardEvent
    Quartz.CGEventSetFlags = CGEventSetFlags
    Quartz.CGEventKeyboardSetUnicodeString = CGEventKeyboardSetUnicodeString
    Quartz.CGEventCreateScrollWheelEvent = CGEventCreateScrollWheelEvent
    Quartz.CGEventSetIntegerValueField = CGEventSetIntegerValueField
    Quartz.CGWindowListCopyWindowInfo = CGWindowListCopyWindowInfo
    Quartz.CGGetActiveDisplayList = CGGetActiveDisplayList
    Quartz.CGDisplayBounds = CGDisplayBounds
    Quartz.CGRectUnion = CGRectUnion

    ApplicationServices.AXIsProcessTrusted = lambda: True

    AppKit.NSApplicationActivateIgnoringOtherApps = 2

    class FakeNSRunningApplication:
        def __init__(self, pid):
            self.pid = pid
            self.activated = False

        def activateWithOptions_(self, options):
            self.activated = True

        def processIdentifier(self):
            return self.pid

    def runningApplicationWithProcessIdentifier(pid):
        return FakeNSRunningApplication(pid)

    class FakeNSWorkspace:
        def frontmostApplication(self):
            return FakeNSRunningApplication(501)

        @classmethod
        def sharedWorkspace(cls):
            return cls()

    AppKit.NSRunningApplication = type(
        "NSRunningApplication", (), {
            "runningApplicationWithProcessIdentifier":
                staticmethod(runningApplicationWithProcessIdentifier)})
    AppKit.NSWorkspace = FakeNSWorkspace

    class FakeNSScreen:
        @classmethod
        def mainScreen(cls):
            return cls()

        def backingScaleFactor(self):
            return 2.0

    AppKit.NSScreen = FakeNSScreen

    sys.modules["Quartz"] = Quartz
    sys.modules["AppKit"] = AppKit
    sys.modules["ApplicationServices"] = ApplicationServices
    return Quartz, calls


@pytest.fixture
def mac_env():
    return _install_fake_pyobjc()


@pytest.fixture
def provider(mac_env):
    from reasonix_computer_use.platform.macos import MacOSPlatformProvider
    return MacOSPlatformProvider()


# ── Permission gating ────────────────────────────────────────────────────

def test_accessibility_required(monkeypatch, provider):
    import ApplicationServices
    monkeypatch.setattr(ApplicationServices, "AXIsProcessTrusted",
                        lambda: False)
    with pytest.raises(OSError, match="Accessibility"):
        provider.mouse_click(10, 10)
    with pytest.raises(OSError, match="Accessibility"):
        provider.keyboard_type("hi")


# ── Keysym / keycode mapping ─────────────────────────────────────────────

def test_keycode_mapping(provider, mac_env):
    Quartz = mac_env[0]
    assert provider._resolve_keycode("enter") == Quartz.kVK_Return
    assert provider._resolve_keycode("cmd") == Quartz.kVK_Command
    assert provider._resolve_keycode("win") == Quartz.kVK_Command
    assert provider._resolve_keycode("ctrl") == Quartz.kVK_Control
    assert provider._resolve_keycode("alt") == Quartz.kVK_Option
    assert provider._resolve_keycode("space") == Quartz.kVK_Space
    with pytest.raises(ValueError):
        provider._resolve_keycode("not_a_key")


# ── Mouse timing ─────────────────────────────────────────────────────────

def test_mouse_click_human_timing(provider, mac_env, monkeypatch):
    _, calls = mac_env
    sleeps: list[float] = []
    monkeypatch.setattr("reasonix_computer_use.platform.macos.time.sleep",
                        lambda s: sleeps.append(s))

    provider.mouse_click(200, 200, count=2)

    events = [c for c in calls if c["kind"] == "mouse"]
    types = [e["type"] for e in events]
    assert types == [17, 11, 12, 11, 12]  # move, down, up, down, up
    assert sleeps == [0.03, 0.06, 0.20, 0.06]  # MOVE_SETTLE, PRESS_HOLD, CLICK_GAP, PRESS_HOLD
    # 200px at scale 2.0 -> 100 points
    assert events[0]["pos"] == (100.0, 100.0)


def test_mouse_click_unknown_button(provider):
    with pytest.raises(ValueError):
        provider.mouse_click(0, 0, button="side")


# ── Keyboard ─────────────────────────────────────────────────────────────

def test_keyboard_type_unicode(provider, mac_env):
    _, calls = mac_env
    provider.keyboard_type("你好abc")
    key_events = [c for c in calls if c["kind"] == "key"]
    # one down event + one up event, each carrying the full string
    assert len(key_events) == 2
    assert key_events[0]["event"]["text"] == "你好abc"
    assert key_events[0]["event"]["down"] is True
    assert key_events[1]["event"]["down"] is False


def test_keyboard_press_combination(provider, mac_env):
    Quartz, calls = mac_env
    provider.keyboard_press(["cmd", "f5"])
    key_events = [c for c in calls if c["kind"] == "key"]
    assert len(key_events) == 2
    down, up = key_events[0]["event"], key_events[1]["event"]
    assert down["keycode"] == Quartz.kVK_F5
    assert down["flags"] == Quartz.kCGEventFlagMaskCommand
    assert up["down"] is False


def test_keyboard_press_unknown_key(provider):
    with pytest.raises(ValueError):
        provider.keyboard_press(["not_a_key"])


# ── Windows / scale ──────────────────────────────────────────────────────

def test_list_windows_applies_scale(provider):
    windows = provider.list_windows()
    assert len(windows) == 1  # empty-title window filtered
    win = windows[0]
    assert win.title == "Test Window"
    assert win.process_id == 501
    # 800x600 points at scale 2.0 -> 1600x1200 physical pixels
    assert win.rect == (0, 0, 1600, 1200)
    assert win.scale_factor == 2.0
    assert win.id == "101"


def test_foreground_window(provider):
    win = provider.get_foreground_window()
    assert win is not None and win.process_id == 501


def test_activate_window(provider):
    assert provider.activate_window("101") is True


# ── Recording ────────────────────────────────────────────────────────────

def test_start_recording_builds_avfoundation_args(provider, mac_env):
    rec = mock.Mock()
    provider._recorder = rec
    provider.start_recording(Path("/tmp/o.mp4"), (0, 0, 1920, 1080))
    args_builder = rec.start.call_args.args[1]
    args = args_builder((0, 0, 1920, 1080))
    assert "avfoundation" in args
    assert "1:none" in args
    assert "crop=1920:1080:0:0" in " ".join(args)
    assert str(Path("/tmp/o.mp4")) in args


# ── Virtual screen ───────────────────────────────────────────────────────

def test_virtual_screen_rect_scaled(provider):
    left, top, right, bottom = provider.get_virtual_screen_rect()
    assert (left, top, right, bottom) == (0, 0, 2880, 1800)
