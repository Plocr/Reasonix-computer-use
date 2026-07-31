"""Tests for the Linux AT-SPI2 precision provider.

The AT-SPI2 D-Bus stack is not available on the dev machine, so a fake
``gi.repository.Atspi`` is injected and ``sys.platform`` is patched to
``linux``.  The tests verify the same structured output contract as the
Windows UIA provider (stable ids, UIA-style roles, physical bboxes),
filtering, dedupe and revision semantics.
"""

from __future__ import annotations

import sys
import types
import unittest.mock as mock
from types import SimpleNamespace

import pytest


def _install_fake_gi() -> "tuple[Any, Any]":
    """Install a fake gi.repository.Atspi into sys.modules.

    Returns (atspi_module, node_factory_helpers).
    """
    gi = types.ModuleType("gi")
    repo = types.ModuleType("gi.repository")
    atspi = types.ModuleType("gi.repository.Atspi")

    gi.require_version = mock.Mock()
    gi.repository = repo
    repo.Atspi = atspi

    atspi.StateType = SimpleNamespace(ENABLED=1, SHOWING=2, ACTIVE=3)
    atspi.CoordType = SimpleNamespace(SCREEN=1)
    atspi.init = mock.Mock()

    class Node:
        def __init__(self, role, name, rect, enabled=True, showing=True,
                     active=False, pid=0, children=None):
            self.role = role
            self.name = name
            self.rect = rect          # (left, top, right, bottom)
            self.enabled = enabled
            self.showing = showing
            self.active = active
            self.pid = pid
            self.children = children or []

        def get_role_name(self):
            return self.role

        def get_name(self):
            return self.name

        def get_child_count(self):
            return len(self.children)

        def get_child_at_index(self, index):
            return self.children[index]

        def get_state_set(self):
            def contains(state):
                return {
                    1: self.enabled,
                    2: self.showing,
                    3: self.active,
                }.get(state, False)
            return SimpleNamespace(contains=contains)

        def get_extents(self, coord_type):
            left, top, right, bottom = self.rect
            return (left, top, right - left, bottom - top)

        def get_process_id(self):
            return self.pid

    desktop = Node("application", "desktop", (0, 0, 1920, 1080), children=[
        Node("application", "app1", (0, 0, 1920, 1080), pid=100, children=[
            Node("frame", "Window 1", (0, 0, 800, 600), active=True, children=[
                Node("push button", "OK", (10, 10, 80, 30)),
                Node("text", "Username", (10, 50, 200, 80)),
                Node("check box", "Remember", (10, 90, 150, 110),
                     enabled=False),            # disabled → filtered
                Node("label", "Hello", (10, 120, 100, 140)),  # non-interactive → filtered
                Node("push button", "OK", (10, 10, 80, 30)),  # duplicate → deduped
            ]),
        ]),
        Node("application", "app2", (0, 0, 1920, 1080), pid=200, children=[
            Node("frame", "Window 2", (0, 0, 400, 300), children=[
                Node("link", "Docs", (5, 5, 60, 25)),
            ]),
        ]),
    ])

    atspi.get_desktop = mock.Mock(return_value=desktop)

    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repo
    sys.modules["gi.repository.Atspi"] = atspi
    return atspi, Node


@pytest.fixture
def atspi_env(monkeypatch):
    # Import the module chain FIRST while sys.platform is still win32:
    # perception/__init__ imports easy_ocr -> numpy, and numpy's init
    # calls os.uname() when sys.platform == "linux" (absent on Windows).
    # Caching the modules avoids re-importing under the patched platform.
    from reasonix_computer_use.perception.precision.linux_atspi import (  # noqa: F401
        LinuxATSPI2Precision)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    return _install_fake_gi()


@pytest.fixture
def provider(atspi_env):
    from reasonix_computer_use.perception.precision.linux_atspi import (
        LinuxATSPI2Precision)
    return LinuxATSPI2Precision()


def test_available_on_linux_x11(atspi_env):
    from reasonix_computer_use.perception.precision.linux_atspi import (
        LinuxATSPI2Precision)
    assert LinuxATSPI2Precision().available is True


def test_unavailable_on_wayland(atspi_env, monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    from reasonix_computer_use.perception.precision.linux_atspi import (
        LinuxATSPI2Precision)
    assert LinuxATSPI2Precision().available is False


def test_observe_active_window(atspi_env, provider):
    snap = provider.observe(None, 80)
    assert snap.source == "precision"
    assert snap.window_id == "active"
    assert snap.width == 800 and snap.height == 600
    # "OK" button + "Username" edit; disabled checkbox, label and the
    # duplicate "OK" are all filtered out
    assert [(e.text, e.role) for e in snap.elements] == [
        ("OK", "Button"), ("Username", "Edit")]
    assert snap.elements[0].bbox == (10, 10, 80, 30)
    assert snap.elements[0].id.startswith("e")


def test_observe_revision_increments(atspi_env, provider):
    assert provider.observe(None, 80).revision == 1
    assert provider.observe(None, 80).revision == 2


def test_observe_by_pid(atspi_env, provider):
    snap = provider.observe("app:200", 80)
    assert snap.window_id == "app:200"
    assert [(e.text, e.role) for e in snap.elements] == [
        ("Docs", "Hyperlink")]


def test_observe_max_elements(atspi_env, provider):
    # Force a small cap; the walk must stop early and still return valid data
    snap = provider.observe(None, 1)
    assert len(snap.elements) <= 1


def test_observe_unknown_pid_returns_empty(atspi_env, provider):
    snap = provider.observe("app:999", 80)
    assert snap.elements == []


def test_role_mapping_covers_uia_vocabulary():
    from reasonix_computer_use.perception.precision.linux_atspi import (
        ATSPI_ROLE_MAP)
    expected_roles = {"Button", "CheckBox", "ComboBox", "Edit", "Hyperlink",
                      "ListItem", "MenuItem", "RadioButton", "ScrollBar",
                      "Slider", "Spinner", "TabItem", "TreeItem", "DataItem",
                      "Document"}
    assert set(ATSPI_ROLE_MAP.values()) == expected_roles
