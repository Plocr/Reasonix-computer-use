"""Pytest tests for platform module import and interface conformance."""
import pytest
from reasonix_computer_use.platform import PlatformProvider, WindowsPlatformProvider, WindowInfo


def test_platform_provider_is_abstract():
    """PlatformProvider should be abstract (cannot instantiate directly)."""
    import abc
    assert abc.ABC in PlatformProvider.__mro__


def test_windows_provider_instantiable():
    """WindowsPlatformProvider should be instantiable."""
    wp = WindowsPlatformProvider()
    assert wp._dpi_mode in ("per-monitor-v2", "per-monitor", "system", "unaware")


def test_windows_provider_has_all_methods():
    """WindowsPlatformProvider must implement all PlatformProvider methods."""
    required = {
        "mouse_move", "mouse_click", "mouse_drag", "mouse_scroll",
        "keyboard_type", "keyboard_press", "keyboard_key_down", "keyboard_key_up",
        "screenshot", "get_virtual_screen_rect",
        "list_windows", "get_window_rect", "activate_window", "get_foreground_window",
        "start_recording", "stop_recording",
    }
    wp = WindowsPlatformProvider()
    for method_name in required:
        assert hasattr(wp, method_name), f"Missing method: {method_name}"
        method = getattr(wp, method_name)
        assert callable(method), f"{method_name} is not callable"


def test_window_info_dataclass():
    wi = WindowInfo(
        id="12345", title="Test Window",
        process_name="test.exe", process_id=42,
        rect=(100, 50, 1100, 818),
        dpi=144, scale_factor=1.5,
    )
    assert wi.id == "12345"
    assert wi.title == "Test Window"
    assert wi.scale_factor == 1.5
    assert wi.rect[2] - wi.rect[0] == 1000


def test_virtual_screen_rect():
    """Verify get_virtual_screen_rect returns a sensible best-fit rect."""
    wp = WindowsPlatformProvider()
    left, top, right, bottom = wp.get_virtual_screen_rect()
    assert right > left > -10000
    assert bottom > top > -10000
    # Screen should not be larger than 32768 (Win32 max)
    assert (right - left) <= 32768
    assert (bottom - top) <= 32768


def test_list_windows_returns_list():
    wp = WindowsPlatformProvider()
    windows = wp.list_windows()
    assert isinstance(windows, list)
    for w in windows:
        assert isinstance(w, WindowInfo)
        assert w.title
        assert len(w.rect) == 4
        assert w.rect[2] > w.rect[0]
        assert w.rect[3] > w.rect[1]
