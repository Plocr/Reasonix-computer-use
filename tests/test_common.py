"""Tests for the platform-agnostic helpers in platform/common.py.

These run on every platform (Windows / macOS / Linux) — they are the
single-source-of-truth contract shared by all OS backends.
"""

from __future__ import annotations

import sys
import unittest.mock as mock
from pathlib import Path

import pytest

from reasonix_computer_use.platform import common


def test_timing_constants_single_source():
    assert common.PRESS_HOLD == 0.06
    assert common.CLICK_GAP == 0.20
    assert common.MOVE_SETTLE == 0.03
    assert common.SCROLL_SETTLE == 0.05
    assert common.MAX_DURATION == 5.0
    assert common.MAX_CLICK_COUNT == 10


def test_clamp_count():
    assert common.clamp_count(0) == 1
    assert common.clamp_count(2) == 2
    assert common.clamp_count(99) == common.MAX_CLICK_COUNT


def test_clamp_duration():
    assert common.clamp_duration(0.0) == 0.05
    assert common.clamp_duration(0.5) == 0.5
    assert common.clamp_duration(99) == common.MAX_DURATION


def test_normalize_key_name_aliases():
    assert common.normalize_key_name("CTRL") == "ctrl"
    assert common.normalize_key_name("Control") == "ctrl"
    assert common.normalize_key_name("cmd") == "super"
    assert common.normalize_key_name("win") == "super"
    assert common.normalize_key_name("meta") == "super"
    assert common.normalize_key_name("command") == "super"
    assert common.normalize_key_name("opt") == "alt"
    assert common.normalize_key_name("altgr") == "alt"
    # Non-alias keys pass through unchanged
    assert common.normalize_key_name("enter") == "enter"
    assert common.normalize_key_name("F5") == "f5"


def test_drag_plan_linear_interpolation():
    points, delay = common.drag_plan((0, 0), (100, 50), 0.5)
    assert len(points) == 20
    assert abs(delay - 0.025) < 1e-9
    assert points[0] == (5, 2)
    assert points[-1] == (100, 50)


def test_drag_plan_clamps_duration():
    points, delay = common.drag_plan((0, 0), (10, 10), 99)
    assert len(points) <= 60
    assert delay > 0


def test_ffmpeg_recorder_lifecycle():
    proc = mock.Mock()
    proc.poll.return_value = None
    rec = common.FfmpegRecorder()
    out = Path("/tmp/rec.mp4")
    with mock.patch("reasonix_computer_use.platform.common.subprocess.Popen",
                    return_value=proc), \
         mock.patch("reasonix_computer_use.platform.common.time.sleep"):
        assert rec.start(out, lambda rect: ["ffmpeg", "-i", "desktop", str(out)]) is True
        assert rec._output == out
        assert rec.stop() == out
        # Second stop is a safe no-op
        assert rec.stop() is None
    proc.terminate.assert_called_once()


def test_ffmpeg_recorder_immediate_crash_probe():
    proc = mock.Mock()
    proc.poll.return_value = 1
    rec = common.FfmpegRecorder()
    with mock.patch("reasonix_computer_use.platform.common.subprocess.Popen",
                    return_value=proc), \
         mock.patch("reasonix_computer_use.platform.common.time.sleep"):
        assert rec.start(Path("/tmp/r.mp4"), lambda r: ["ffmpeg"]) is False


def test_ffmpeg_recorder_missing_binary():
    rec = common.FfmpegRecorder()
    with mock.patch("reasonix_computer_use.platform.common.subprocess.Popen",
                    side_effect=FileNotFoundError), \
         mock.patch("reasonix_computer_use.platform.common.time.sleep"):
        assert rec.start(Path("/tmp/r.mp4"), lambda r: ["ffmpeg"]) is False


@pytest.mark.skipif(sys.platform != "win32",
                    reason="VK resolution is Windows-only")
def test_resolve_vk_equivalent_after_refactor():
    """Key-name resolution must be unchanged after the common.py extraction."""
    from reasonix_computer_use.platform.windows import _resolve_vk

    cases = {
        "win": 0x5B, "cmd": 0x5B, "super": 0x5B, "meta": 0x5B, "command": 0x5B,
        "option": 0x12, "opt": 0x12, "alt": 0x12,
        "control": 0x11, "ctrl": 0x11, "altgr": 0x12,
        "enter": 0x0D, "space": 0x20, "f5": 0x74, "a": 0x41, "A": 0x41,
    }
    for name, expected in cases.items():
        assert _resolve_vk(name) == expected, f"key {name!r}"
