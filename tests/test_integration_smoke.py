"""Integration smoke test for Computer Use tools.

Verifies all tools import correctly and have their expected interfaces.
Does NOT perform destructive mouse/keyboard actions.
"""
import sys, json

sys.path.insert(0, ".")

def test_imports():
    """All core modules must import without error."""
    modules = [
        "reasonix_computer_use.protocol.coordinates",
        "reasonix_computer_use.platform.linux",
        "reasonix_computer_use.perception.router",
        "reasonix_computer_use.perception.precision.linux_atspi",
        "reasonix_computer_use.perception.vision.easy_ocr",
        "reasonix_computer_use.services.system_profiler",
        "reasonix_computer_use.tools.screen_interactor",
        "reasonix_computer_use.tools.computer_system",
        "reasonix_computer_use.tools.web_navigator",
        "reasonix_computer_use.tools.hidden.actions",
        "reasonix_computer_use.mcp_server",
        "reasonix_computer_use.environment_setup",
    ]
    # Windows-only modules (ctypes.windll at module level) — only on win32
    if sys.platform == "win32":
        modules += [
            "reasonix_computer_use.platform.windows",
            "reasonix_computer_use.perception.precision.windows_uia",
        ]
    for mod in modules:
        import importlib
        m = importlib.import_module(mod)
        print(f"  OK: {mod} ({m.__name__})")

def test_platform_interface():
    """PlatformProvider must expose all required methods."""
    from reasonix_computer_use.platform import get_platform
    plat = get_platform()
    methods = [
        "mouse_move", "mouse_click", "mouse_drag", "mouse_scroll",
        "keyboard_type", "keyboard_press", "keyboard_key_down", "keyboard_key_up",
        "screenshot", "get_virtual_screen_rect",
        "list_windows", "get_window_rect", "activate_window", "get_foreground_window",
        "start_recording", "stop_recording",
    ]
    for m in methods:
        assert hasattr(plat, m), f"Missing: {m}"
        assert callable(getattr(plat, m)), f"Not callable: {m}"
    print(f"  OK: {len(methods)} methods on PlatformProvider")

def test_screen_interactor_interface():
    """ScreenInteractor must have observe + execute."""
    from reasonix_computer_use.tools.screen_interactor import ScreenInteractor
    si = ScreenInteractor()
    assert hasattr(si, "observe"), "Missing observe"
    assert hasattr(si, "execute"), "Missing execute"
    assert si._was_blocked is False
    print(f"  OK: ScreenInteractor ready (blocked={si._was_blocked})")

def test_hidden_tools_interface():
    """Hidden tools must expose their operations."""
    from reasonix_computer_use.tools.hidden.actions import (
        HiddenMouse, HiddenKeyboard, HiddenScreenshot, HiddenScreenRecorder
    )
    mouse = HiddenMouse()
    kb = HiddenKeyboard()
    ss = HiddenScreenshot()
    rec = HiddenScreenRecorder()

    assert hasattr(mouse, "click"), "Missing mouse.click"
    assert hasattr(mouse, "drag"), "Missing mouse.drag"
    assert hasattr(mouse, "scroll"), "Missing mouse.scroll"
    assert hasattr(kb, "type"), "Missing keyboard.type"
    assert hasattr(kb, "press"), "Missing keyboard.press"
    assert hasattr(ss, "capture"), "Missing screenshot.capture"
    assert hasattr(rec, "start"), "Missing recorder.start"
    assert hasattr(rec, "stop"), "Missing recorder.stop"
    print(f"  OK: Hidden tools (mouse/kb/screenshot/recorder) ready")

def test_tools_registry():
    """All 10 tools must be registered."""
    from reasonix_computer_use.tools import _get_si
    from reasonix_computer_use.mcp_server import TOOLS
    # Force tool registration
    import reasonix_computer_use.tools  # noqa
    names = sorted(TOOLS.keys())
    expected = {"screen_interactor", "computer_system", "web_navigator",
                "computer_app", "computer_state", "computer_action",
                "mouse_action", "keyboard_action", "screenshot", "screen_recorder"}
    assert set(names) == expected, f"Missmatch: {set(names) ^ expected}"
    print(f"  OK: {len(names)} tools registered")

def test_action_types():
    """All common action types must be recognized."""
    from reasonix_computer_use.tools.screen_interactor import ALL_ACTIONS
    for t in ("click", "click_ref", "click_point", "type", "press", "press_key",
              "key", "enter", "submit", "scroll", "hover", "drag", "wait", "screenshot"):
        assert t in ALL_ACTIONS, f"Missing action type: {t}"
    print(f"  OK: {len(ALL_ACTIONS)} action types recognized")

if __name__ == "__main__":
    print("Computer Use Integration Test v0.9.0-preview\n")
    test_imports()
    test_platform_interface()
    test_screen_interactor_interface()
    test_hidden_tools_interface()
    test_tools_registry()
    test_action_types()
    print("\nALL TESTS PASSED")
