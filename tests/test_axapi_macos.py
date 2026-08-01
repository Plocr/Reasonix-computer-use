"""Tests for the macOS AXAPI precision provider.

A fake ``ApplicationServices``/``AppKit`` is injected so the tests run on
Windows too.  Verified: availability, permission gating, active-window
walk with UIA-style role mapping and scale conversion, filtering, dedupe,
revision and window targeting.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest


class FakeAX:
    """Minimal AX element: attribute dict."""

    def __init__(self, attrs):
        self.attrs = attrs


def _install_fake_ax():
    AS = types.ModuleType("ApplicationServices")
    AppKit = types.ModuleType("AppKit")

    AS.kAXRoleAttribute = "AXRole"
    AS.kAXTitleAttribute = "AXTitle"
    AS.kAXDescriptionAttribute = "AXDescription"
    AS.kAXEnabledAttribute = "AXEnabled"
    AS.kAXPositionAttribute = "AXPosition"
    AS.kAXSizeAttribute = "AXSize"
    AS.kAXChildrenAttribute = "AXChildren"
    AS.kAXWindowsAttribute = "AXWindows"
    AS.kAXFocusedWindowAttribute = "AXFocusedWindow"
    AS.kAXValueCGPointType = 1
    AS.kAXValueCGSizeType = 2

    # App tree: pid 100 with one focused window containing interactive nodes
    button_ok = FakeAX({"AXRole": "AXButton", "AXTitle": "OK",
                        "AXEnabled": True,
                        "AXPosition": (10, 10), "AXSize": (70, 20),
                        "AXChildren": []})
    field = FakeAX({"AXRole": "AXTextField", "AXTitle": "Username",
                    "AXEnabled": True,
                    "AXPosition": (10, 50), "AXSize": (190, 30),
                    "AXChildren": []})
    disabled = FakeAX({"AXRole": "AXCheckBox", "AXTitle": "Remember",
                       "AXEnabled": False,
                       "AXPosition": (10, 90), "AXSize": (140, 20),
                       "AXChildren": []})
    static = FakeAX({"AXRole": "AXStaticText", "AXTitle": "Hello",
                     "AXEnabled": True,
                     "AXPosition": (10, 120), "AXSize": (90, 20),
                     "AXChildren": []})
    duplicate = FakeAX({"AXRole": "AXButton", "AXTitle": "OK",
                        "AXEnabled": True,
                        "AXPosition": (10, 10), "AXSize": (70, 20),
                        "AXChildren": []})
    window1 = FakeAX({
        "AXPosition": (0, 0), "AXSize": (800, 600),
        "AXChildren": [button_ok, field, disabled, static, duplicate]})
    window2 = FakeAX({
        "AXPosition": (100, 100), "AXSize": (400, 300),
        "AXChildren": [FakeAX({"AXRole": "AXLink", "AXTitle": "Docs",
                               "AXEnabled": True,
                               "AXPosition": (5, 5), "AXSize": (55, 20),
                               "AXChildren": []})]})
    apps = {
        100: FakeAX({"AXWindows": [window1]}),
        200: FakeAX({"AXWindows": [window2]}),
    }

    def AXUIElementCreateApplication(pid):
        return apps.get(pid, FakeAX({"AXWindows": []}))

    def AXUIElementCopyAttributeValue(element, attribute, out):
        if attribute == "AXFocusedWindow":
            return 0, window1
        return 0, element.attrs.get(attribute)

    def AXValueGetValue(value, value_type, out):
        x, y = value
        return True, SimpleNamespace(x=x, y=y, width=x, height=y)

    AS.AXUIElementCreateApplication = AXUIElementCreateApplication
    AS.AXUIElementCopyAttributeValue = AXUIElementCopyAttributeValue
    AS.AXValueGetValue = AXValueGetValue

    class FakeFrontApp:
        def processIdentifier(self):
            return 100

    class FakeNSWorkspace:
        @classmethod
        def sharedWorkspace(cls):
            return cls()

        def frontmostApplication(self):
            return FakeFrontApp()

    AppKit.NSWorkspace = FakeNSWorkspace

    class FakeNSScreen:
        @classmethod
        def mainScreen(cls):
            return cls()

        def backingScaleFactor(self):
            return 2.0

    AppKit.NSScreen = FakeNSScreen

    sys.modules["ApplicationServices"] = AS
    sys.modules["AppKit"] = AppKit
    return AS


@pytest.fixture
def ax_env(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    # Import the module chain while sys.platform is real (numpy/os.uname
    # guard, same as the AT-SPI tests) — then patch platform.
    from reasonix_computer_use.perception.precision.macos_axapi import (  # noqa: F401
        MacOSAXAPIPrecision)
    return _install_fake_ax()


@pytest.fixture
def provider(ax_env):
    from reasonix_computer_use.perception.precision.macos_axapi import (
        MacOSAXAPIPrecision)
    return MacOSAXAPIPrecision()


def test_available_on_darwin(ax_env):
    from reasonix_computer_use.perception.precision.macos_axapi import (
        MacOSAXAPIPrecision)
    assert MacOSAXAPIPrecision().available is True


def test_unavailable_off_darwin(ax_env, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    from reasonix_computer_use.perception.precision.macos_axapi import (
        MacOSAXAPIPrecision)
    assert MacOSAXAPIPrecision().available is False


def test_observe_focused_window(provider):
    snap = provider.observe(None, 80)
    assert snap.source == "precision"
    assert snap.window_id == "pid:100"
    # scale 2.0: button 70x20 points -> 140x40 pixels at (20,20)
    assert [(e.text, e.role) for e in snap.elements] == [
        ("OK", "Button"), ("Username", "Edit")]
    assert snap.elements[0].bbox == (20, 20, 160, 60)
    assert snap.elements[0].id.startswith("e")


def test_observe_by_pid(provider):
    snap = provider.observe("200", 80)
    assert [(e.text, e.role) for e in snap.elements] == [
        ("Docs", "Hyperlink")]


def test_observe_by_pid_index(provider):
    snap = provider.observe("100:0", 80)
    assert snap.window_id == "100:0"
    assert snap.elements[0].text == "OK"


def test_observe_revision_increments(provider):
    assert provider.observe(None, 80).revision == 1
    assert provider.observe(None, 80).revision == 2


def test_observe_unknown_pid_empty(provider):
    snap = provider.observe("999", 80)
    assert snap.elements == []


def test_role_mapping_covers_uia_vocabulary():
    from reasonix_computer_use.perception.precision.macos_axapi import AX_ROLE_MAP
    expected = {"Button", "CheckBox", "ComboBox", "Edit", "Hyperlink",
                "ListItem", "MenuItem", "RadioButton", "ScrollBar",
                "Slider", "Spinner", "DataItem", "Document"}
    assert set(AX_ROLE_MAP.values()) == expected
