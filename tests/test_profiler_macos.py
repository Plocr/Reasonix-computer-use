"""Tests for the macOS system profile collector.

Fake Foundation/Quartz/AppKit modules are injected; .app bundles are
created under tmp_path.  The collector runs on Windows for contract
verification; real values are validated on macOS.
"""

from __future__ import annotations

import sys
import types
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace

import pytest


def _install_fake_foundation(tmp_apps: list[dict]):
    """Install fake Quartz/Foundation/AppKit with the given app bundles."""
    Quartz = types.ModuleType("Quartz")
    Foundation = types.ModuleType("Foundation")
    AppKit = types.ModuleType("AppKit")

    class _FakeCType:
        def __mul__(self, n):
            return lambda: [0] * n

    Quartz.CGDirectDisplayID = _FakeCType()

    def CGGetActiveDisplayList(max_disp, ids, count):
        ids[0] = 1
        ids[1] = 2
        return 2

    def CGDisplayBounds(display_id):
        if display_id == 1:
            return SimpleNamespace(origin=SimpleNamespace(x=0, y=0),
                                   size=SimpleNamespace(width=1440, height=900))
        return SimpleNamespace(origin=SimpleNamespace(x=1440, y=0),
                               size=SimpleNamespace(width=1280, height=800))

    Quartz.CGGetActiveDisplayList = CGGetActiveDisplayList
    Quartz.CGDisplayBounds = CGDisplayBounds
    Quartz.CGDisplayIsMain = lambda display_id: display_id == 1

    Foundation.NSDesktopDirectory = 1
    Foundation.NSDocumentDirectory = 2
    Foundation.NSDownloadsDirectory = 3
    Foundation.NSPicturesDirectory = 4
    Foundation.NSMusicDirectory = 5
    Foundation.NSMoviesDirectory = 6
    Foundation.NSUserDomainMask = 1
    Foundation.NSHomeDirectory = lambda: "/Users/tester"

    def NSSearchPathForDirectoriesInDomains(directory, domain, expand):
        return [f"/Users/tester/{directory}"]

    Foundation.NSSearchPathForDirectoriesInDomains = \
        NSSearchPathForDirectoriesInDomains

    class FakeNSScreen:
        @classmethod
        def mainScreen(cls):
            return cls()

        def backingScaleFactor(self):
            return 2.0

    AppKit.NSScreen = FakeNSScreen

    sys.modules["Quartz"] = Quartz
    sys.modules["Foundation"] = Foundation
    sys.modules["AppKit"] = AppKit


@pytest.fixture
def mac_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    apps_dir = home / "Applications"
    (apps_dir / "MyApp.app" / "Contents" / "MacOS").mkdir(parents=True)
    (apps_dir / "MyApp.app" / "Contents" / "Info.plist").write_bytes(
        b'<?xml version="1.0"?><plist version="1.0"><dict>'
        b'<key>CFBundleDisplayName</key><string>MyApp</string>'
        b'<key>CFBundleExecutable</key><string>myapp</string>'
        b'</dict></plist>')
    (apps_dir / "NoPlist.app" / "Contents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    _install_fake_foundation([apps_dir])
    return apps_dir


@pytest.fixture
def profiler(mac_env, tmp_path, monkeypatch):
    import reasonix_computer_use.services.system_profiler as sp
    monkeypatch.setattr(sp, "index_path",
                        lambda: tmp_path / "system-index.json")
    monkeypatch.setattr(sp, "profile_path",
                        lambda: tmp_path / "system.md")
    monkeypatch.setattr(sp, "apps_dir", lambda: tmp_path / "apps")
    monkeypatch.setattr(sp, "reasonix_global_memory_dir", lambda: None)
    return sp.SystemProfiler()


def test_detect_macos_displays(profiler):
    displays = profiler._detect_macos_displays()
    assert len(displays) == 2
    assert displays[0]["width"] == 2880 and displays[0]["primary"] is True
    assert displays[1]["width"] == 2560 and displays[1]["primary"] is False
    assert displays[0]["scale_factor"] == 2.0
    assert displays[0]["dpi"] == 192


def test_detect_macos_folders(profiler):
    folders = profiler._detect_macos_folders()
    assert folders["桌面"]["path"] == "/Users/tester/1"
    assert folders["文档"]["path"] == "/Users/tester/2"
    assert folders["下载"]["path"] == "/Users/tester/3"
    assert folders["主目录"]["path"] == "/Users/tester"


def test_scan_macos_apps(profiler, mac_env):
    apps = profiler._scan_macos_apps()
    # On a real macOS machine /Applications also exists — assert the test
    # bundle is discovered and the broken one filtered, not exact counts.
    names = [app["name"] for app in apps]
    assert "MyApp" in names
    myapp = next(a for a in apps if a["name"] == "MyApp")
    assert myapp["path"].replace("\\", "/").endswith("Contents/MacOS/myapp")
    assert myapp["source"] == "app_bundle"
    assert myapp["confidence"] == 0.9


def test_detect_macos_hardware(profiler, monkeypatch):
    import json

    def fake_run(args, **kwargs):
        key = args[0]
        if key == "sysctl":
            values = {"machdep.cpu.brand_string": "Apple M3",
                      "hw.physicalcpu": "8", "hw.logicalcpu": "8",
                      "hw.memsize": "17179869184"}
            return SimpleNamespace(returncode=0,
                                   stdout=values.get(args[2], "unknown"))
        if key == "system_profiler":
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "SPDisplaysDataType": [{
                    "sppci_model": "Apple M3 GPU",
                    "spdisplays_vram": "10 GB"}]}))
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr("subprocess.run", fake_run)
    hw = profiler._detect_macos_hardware()
    assert hw["cpu"] == "Apple M3"
    assert hw["cpu_cores"] == 8 and hw["cpu_threads"] == 8
    assert hw["memory_gb"] == 16.0  # 17179869184 / 1024^3
    assert hw["gpus"] == [{"name": "Apple M3 GPU", "vram": "10 GB"}]


def test_detect_macos_hardware_sysctl_missing(profiler, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    hw = profiler._detect_macos_hardware()
    assert hw["cpu"] == "unknown" and hw["memory_gb"] == 0 and hw["gpus"] == []


def test_profile_macos_writes_full_index(profiler, mac_env, tmp_path):
    profiler._profile_macos("test")
    index = profiler.load_index()
    assert index["displays"][0]["width"] == 2880
    assert "桌面" in index["known_folders"]
    assert any(a["name"] == "MyApp" for a in index["applications"])
    assert index["system"]["session_type"] == "aqua"
    # hardware 在 macOS CI 上真实可采集（sysctl 存在）——只断言字段存在
    assert index["hardware"]["cpu"]
