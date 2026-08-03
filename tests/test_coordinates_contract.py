"""Contract tests for the coordinate mapping surfaced to host agents.

Root cause from the QQ theme-color task: CLAUDE_1024 mapped to the WINDOW
INTERIOR when a foreground window exists, but the docs said "full display".
These tests pin the real semantics and the transparency fields (window_rect)
added so hosts can verify how their coordinates were interpreted.
"""

from __future__ import annotations

import unittest.mock as mock

import pytest

from reasonix_computer_use.protocol.coordinates import (
    CoordinateConverter, CoordinateSpace, NormalizedCoord)
from reasonix_computer_use.protocol.snapshot import ActionCommand, ScreenSnapshot
from reasonix_computer_use.tools.screen_interactor import _resolve_target


def test_claude_1024_maps_to_window_interior_when_rect_provided():
    """The real mapping used in the QQ task: window-relative."""
    conv = CoordinateConverter(display_width=1920, display_height=1080)
    coord = NormalizedCoord(x=517, y=457, space=CoordinateSpace.CLAUDE_1024)
    x, y = conv.to_physical(coord, window_rect=(769, 234, 1169, 809))
    # 769 + 517/1024*400 = 970.9 → 971 ; 234 + 457/768*575 = 576.2 → 576
    assert (x, y) == (971, 576)


def test_claude_1024_maps_to_full_display_without_rect():
    conv = CoordinateConverter(display_width=1920, display_height=1080)
    coord = NormalizedCoord(x=517, y=457, space=CoordinateSpace.CLAUDE_1024)
    x, y = conv.to_physical(coord)
    # 517/1024*1920 = 969.4 → 969 ; 457/768*1080 = 642.7 → 643
    assert (x, y) == (969, 643)


def test_pixel_space_is_verbatim():
    conv = CoordinateConverter()
    coord = NormalizedCoord(x=969, y=694, space=CoordinateSpace.PIXEL)
    assert conv.to_physical(coord, window_rect=(769, 234, 1169, 809)) == (969, 694)


def test_resolve_target_returns_window_rect_for_fallback():
    conv = CoordinateConverter(display_width=1920, display_height=1080)
    platform = mock.Mock()
    fg = mock.Mock()
    fg.rect = (769, 234, 1169, 809)
    platform.get_foreground_window.return_value = fg

    action = ActionCommand.from_dict({
        "type": "click",
        "fallback": {"x": 512, "y": 545, "space": "CLAUDE_1024"},
    })
    x, y, rect = _resolve_target(action, ScreenSnapshot(0, "", "unknown"),
                                 conv, platform)
    assert (x, y) == (969, 642)  # button center in the QQ login task
    assert rect == (769, 234, 1169, 809)


def test_resolve_target_element_ref_has_no_rect():
    conv = CoordinateConverter()
    platform = mock.Mock()
    snap = ScreenSnapshot(1, "w", "precision", elements=[])
    action = ActionCommand.from_dict({
        "type": "click",
        "element_ref": "missing",
    })
    with pytest.raises(ValueError):
        _resolve_target(action, snap, conv, platform)


def test_out_of_range_error_suggests_pixel_space():
    with pytest.raises(ValueError, match="PIXEL"):
        NormalizedCoord(x=1120, y=252, space=CoordinateSpace.CLAUDE_1024)


def test_execute_reports_window_rect(monkeypatch):
    """The execute response echoes the mapping rect for point actions."""
    from reasonix_computer_use.tools.screen_interactor import ScreenInteractor

    si = ScreenInteractor.__new__(ScreenInteractor)
    si._platform = mock.Mock()
    fg = mock.Mock()
    fg.rect = (769, 234, 1169, 809)
    si._platform.get_foreground_window.return_value = fg
    si._converter = CoordinateConverter(display_width=1920, display_height=1080)
    si._router = mock.Mock()
    si._latest_snapshot = None

    result = si._execute_one(ActionCommand.from_dict({
        "type": "click",
        "fallback": {"x": 512, "y": 545, "space": "CLAUDE_1024"},
    }))
    assert result["status"] == "ok"
    assert result["x"] == 969 and result["y"] == 642
    assert result["window_rect"] == [769, 234, 1169, 809]
    si._platform.mouse_click.assert_called_once_with(969, 642, button="left",
                                                     count=1)
