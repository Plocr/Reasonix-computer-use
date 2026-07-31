"""Tests for the Linux system profile collector.

Uses a fake HOME (tmp_path) with a crafted user-dirs.dirs and .desktop
files, plus mocked mss / Xlib reads, so the collector runs on Windows too.
"""

from __future__ import annotations

import json
import sys
import types
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Fake Linux HOME with XDG config and desktop entries."""
    fake_home = tmp_path / "home"
    (fake_home / ".config").mkdir(parents=True)
    (fake_home / ".local" / "share" / "applications").mkdir(parents=True)
    (fake_home / ".config" / "user-dirs.dirs").write_text(
        'XDG_DESKTOP_DIR="$HOME/桌面"\n'
        'XDG_DOCUMENTS_DIR="$HOME/文档"\n'
        'XDG_DOWNLOAD_DIR="$HOME/下载"\n'
        'XDG_PICTURES_DIR="$HOME/图片"\n'
        'XDG_MUSIC_DIR="$HOME/音乐"\n'
        'XDG_VIDEOS_DIR="$HOME/视频"\n', encoding="utf-8")
    (fake_home / ".local" / "share" / "applications" /
     "myapp.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=MyApp\n"
        "Exec=/usr/bin/myapp --flag\nIcon=myapp\n", encoding="utf-8")
    (fake_home / ".local" / "share" / "applications" /
     "hidden.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Hidden\n"
        "Exec=/usr/bin/hidden\nNoDisplay=true\n", encoding="utf-8")
    (fake_home / ".local" / "share" / "applications" /
     "missing.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Missing\n"
        "Exec=/usr/bin/missing\nTryExec=/usr/bin/definitely-not-installed\n",
        encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake_home))
    return fake_home


@pytest.fixture
def profiler(home, tmp_path, monkeypatch):
    import reasonix_computer_use.services.system_profiler as sp
    # Redirect profile outputs into tmp_path so tests never touch real memory
    monkeypatch.setattr(sp, "index_path",
                        lambda: tmp_path / "system-index.json")
    monkeypatch.setattr(sp, "profile_path",
                        lambda: tmp_path / "system.md")
    monkeypatch.setattr(sp, "apps_dir",
                        lambda: tmp_path / "apps")
    monkeypatch.setattr(sp, "reasonix_global_memory_dir", lambda: None)
    return sp.SystemProfiler()


def test_detect_xdg_folders(profiler, home):
    folders = profiler._detect_xdg_folders()
    assert folders["桌面"]["path"] == str(home / "桌面")
    assert folders["文档"]["path"] == str(home / "文档")
    assert folders["下载"]["path"] == str(home / "下载")
    assert folders["图片"]["path"] == str(home / "图片")
    assert folders["音乐"]["path"] == str(home / "音乐")
    assert folders["视频"]["path"] == str(home / "视频")
    assert folders["主目录"]["path"] == str(home)


def test_detect_xdg_folders_fallback_defaults(tmp_path, monkeypatch):
    """Without user-dirs.dirs, standard English defaults apply."""
    import reasonix_computer_use.services.system_profiler as sp
    bare_home = tmp_path / "bare"
    bare_home.mkdir()
    monkeypatch.setenv("HOME", str(bare_home))
    folders = sp.SystemProfiler._detect_xdg_folders()
    assert folders["桌面"]["path"] == str(bare_home / "Desktop")
    assert folders["文档"]["path"] == str(bare_home / "Documents")


def test_scan_desktop_files(profiler, home, monkeypatch):
    def fake_which(name):
        if name == "/usr/bin/myapp":
            return "/usr/bin/myapp"
        return None
    monkeypatch.setattr(
        "reasonix_computer_use.services.system_profiler.which", fake_which)
    apps = profiler._scan_desktop_files()
    names = [app["name"] for app in apps]
    assert "MyApp" in names
    assert "Hidden" not in names      # NoDisplay=true
    assert "Missing" not in names     # TryExec not installed
    myapp = next(a for a in apps if a["name"] == "MyApp")
    assert myapp["path"] == "/usr/bin/myapp"
    assert myapp["command"] == "/usr/bin/myapp --flag"
    assert myapp["source"] == "desktop"
    assert myapp["confidence"] == 0.9


def test_detect_linux_displays_via_mss(profiler, monkeypatch):
    fake_sct = mock.Mock()
    fake_sct.monitors = [
        {"left": 0, "top": 0, "width": 3200, "height": 1080},
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": 1920, "top": 0, "width": 1280, "height": 1024},
    ]
    fake_mss = mock.MagicMock()
    fake_mss.mss.return_value.__enter__.return_value = fake_sct
    monkeypatch.setattr(
        "reasonix_computer_use.services.system_profiler.mss", fake_mss)
    monkeypatch.setattr(
        "reasonix_computer_use.services.system_profiler.SystemProfiler._xdpi_scale",
        staticmethod(lambda: 1.0))
    displays = profiler._detect_linux_displays()
    assert len(displays) == 2
    assert displays[0]["width"] == 1920 and displays[0]["primary"] is True
    assert displays[1]["width"] == 1280 and displays[1]["primary"] is False
    assert displays[0]["scale_factor"] == 1.0
    assert displays[0]["dpi"] == 96


def test_detect_linux_displays_undetected_marker(profiler, monkeypatch):
    monkeypatch.setattr(
        "reasonix_computer_use.services.system_profiler.mss", None)
    displays = profiler._detect_linux_displays()
    assert len(displays) == 1
    assert displays[0]["detected"] is False
    assert displays[0]["scale_factor"] == 0.0


def test_detect_linux_hardware_from_proc(profiler, monkeypatch):
    cpuinfo = "processor : 0\nmodel name : Fake CPU X\nprocessor : 1\n"
    meminfo = "MemTotal:       16384000 kB\n"

    def fake_read_text(self, *args, **kwargs):
        key = str(self).replace("\\", "/")
        return {"/proc/cpuinfo": cpuinfo, "/proc/meminfo": meminfo}[key]

    monkeypatch.setattr(
        "reasonix_computer_use.services.system_profiler.Path.read_text",
        fake_read_text)
    hw = profiler._detect_linux_hardware()
    assert hw["cpu"] == "Fake CPU X"
    assert hw["cpu_threads"] == 2
    assert hw["memory_gb"] == 15.6  # 16384000 kB / 1024 / 1024


def test_profile_linux_writes_full_index(profiler, home, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "reasonix_computer_use.services.system_profiler.SystemProfiler._detect_linux_displays",
        staticmethod(lambda: [{"width": 1920, "height": 1080, "dpi": 96,
                               "scale_factor": 1.0, "scale_percent": 100,
                               "primary": True, "left": 0, "top": 0,
                               "name": "Display 1"}]))
    monkeypatch.setattr(
        "reasonix_computer_use.services.system_profiler.which",
        lambda name: "/usr/bin/myapp" if name == "/usr/bin/myapp" else None)
    profiler._profile_linux("test")
    index = profiler.load_index()
    assert index["displays"][0]["width"] == 1920
    assert "桌面" in index["known_folders"]
    assert any(a["name"] == "MyApp" for a in index["applications"])
    assert index["system"]["session_type"] == "unknown"
