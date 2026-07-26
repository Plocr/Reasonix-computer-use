"""Contract and unit tests for the 0.8 Reasonix domain API."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


PUBLIC_TOOLS = {"screen_interactor", "computer_system", "web_navigator",
                  "computer_app", "computer_state", "computer_action"}


def test_public_tools_are_registered():
    """All 6 public tools + 4 hidden tools must be registered."""
    from reasonix_computer_use import tools  # noqa: F401
    from reasonix_computer_use.mcp_server import TOOLS

    assert set(TOOLS) == PUBLIC_TOOLS | {"mouse_action", "keyboard_action", "screenshot", "screen_recorder"}
    for tool in TOOLS.values():
        assert len(tool["description"]) > 20
        assert tool["inputSchema"]["type"] == "object"
        assert asyncio.iscoroutinefunction(tool["handler"])


def test_action_schema_exposes_canonical_type_and_coordinate_space():
    from reasonix_computer_use import tools  # noqa: F401
    from reasonix_computer_use.mcp_server import TOOLS

    # screen_interactor is the primary action tool in beta.2
    item = TOOLS["screen_interactor"]["inputSchema"]
    assert item["type"] == "object"
    assert "mode" in item["properties"]
    assert item["properties"]["mode"]["enum"] == ["observe", "execute"]
    # execute mode has actions array
    assert "actions" in item["properties"]


@pytest.mark.asyncio
async def test_mcp_initialize_and_list_report_08():
    from reasonix_computer_use.mcp_server import handle_initialize, handle_tools_list

    initialized = await handle_initialize(1)
    assert initialized["result"]["serverInfo"]["version"] == "0.8.0-beta.3"
    listed = await handle_tools_list(2)
    assert {tool["name"] for tool in listed["result"]["tools"]} == PUBLIC_TOOLS


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected():
    from reasonix_computer_use.mcp_server import handle_tools_call

    result = await handle_tools_call(1, {"name": "computer_screenshot", "arguments": {}})
    assert result["error"]["code"] == -32601


def test_parse_result_is_compact_and_keeps_chinese():
    from reasonix_computer_use.utils import parse_result

    value = parse_result({"text": "设置", "ok": True})
    assert "设置" in value
    assert ": " not in value


def test_window_revision_changes_only_with_state():
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    assert context.update({"title": "QQ"}, "uia") is True
    first = context.revision
    assert context.update({"title": "QQ"}, "uia") is False
    assert context.revision == first
    assert context.update({"title": "QQ设置"}, "uia") is True
    assert context.revision != first


def test_window_context_does_not_recover_by_partial_title(monkeypatch):
    from reasonix_computer_use import runtime
    from reasonix_computer_use.windows import WindowInfo

    context = runtime.WindowContext("w1", 1, app_name="WPS", owner_pid=10)
    edge = WindowInfo(2, "WPS - 搜索 - Microsoft Edge", "Chrome_WidgetWin_1",
                      (0, 0, 800, 600), 20, r"C:\Edge\msedge.exe")
    monkeypatch.setattr(runtime.user32, "IsWindow", lambda _hwnd: False)
    monkeypatch.setattr(runtime, "list_windows", lambda: [edge])
    with pytest.raises(ValueError):
        context.info()


def test_window_context_adopts_new_hwnd_and_ownership(monkeypatch):
    from reasonix_computer_use import runtime
    from reasonix_computer_use.windows import WindowInfo

    context = runtime.WindowContext("w1", 1, app_name="QQ", owner_pid=10)
    context.references = {"e1": {"ref": "e1"}}
    context.image_hash = "old"
    old_revision = context.revision
    replacement = WindowInfo(2, "QQ", "QQWindow", (0, 0, 1000, 700), 20,
                             r"E:\QQ\QQ.exe")
    monkeypatch.setattr(runtime.user32, "IsWindow", lambda _hwnd: False)
    monkeypatch.setattr(runtime.user32, "GetForegroundWindow", lambda: 2)
    monkeypatch.setattr(runtime, "list_windows", lambda: [replacement])

    adopted = context.info()
    assert adopted.hwnd == 2
    assert context.hwnd == 2
    assert context.owner_pid == 20
    assert context.app_path == r"E:\QQ\QQ.exe"
    assert context.revision != old_revision
    assert context.references == {}
    assert context.image_hash == ""


def test_switching_perception_channel_does_not_create_fake_revision():
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "A", "elements": ["button"]}, "uia")
    revision = context.revision
    assert context.update({"window": "A", "texts": ["button"]}, "ocr") is False
    assert context.revision == revision
    assert context.update({"window": "A", "texts": ["button"]}, "ocr") is False
    assert context.update({"window": "A", "texts": ["new"]}, "ocr") is True


def test_unchanged_action_is_not_written_to_memory(monkeypatch, tmp_path):
    from reasonix_computer_use import runtime

    monkeypatch.setattr(runtime, "memory_dir", lambda: tmp_path)
    context = runtime.WindowContext("w1", 1, app_id="excel", app_name="Excel")
    runtime.remember_success(context, {"type": "press", "keys": ["CTRL", "O"]}, "same", "same")
    assert not list(tmp_path.rglob("*.json"))


def test_two_same_state_failures_escalate_strategy():
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "QQ"}, "uia")
    assert context.fail() == 1
    assert context.fail() == 2
    assert context.strategy_level == 2
    assert context.hard_blocked is False

    assert context.fail() == 2
    assert context.fail() == 3
    assert context.strategy_level == 3
    assert context.hard_blocked is False


def test_two_invalid_actions_trip_window_circuit_breaker():
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "QQ音乐"}, "uia")
    assert context.invalid_action() is False
    assert context.invalid_action() is True
    assert context.hard_blocked is True


def test_repeated_observation_advances_channels_before_circuit_breaker():
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "QQ音乐"}, "uia")
    assert context.state_read() is False
    assert context.state_read() is False
    assert context.strategy_level == 2
    assert context.state_read() is False
    assert context.state_read() is False
    assert context.strategy_level == 3
    assert context.state_read() is False
    assert context.state_read() is True
    context.succeed()
    assert context.state_reads_without_action == 0
    assert context.hard_blocked is False


def test_visual_point_defaults_to_window_physical_pixels():
    from reasonix_computer_use.domain_tools import _point
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "A", "A", (-500, 200, 500, 900))
    assert _point(context, {"x": 100, "y": 50}) == (-400, 250)
    assert _point(context, {"x": -400, "y": 250, "coordinate_space": "screen"}) == (-400, 250)


def test_state_elements_use_window_local_physical_pixels():
    from reasonix_computer_use.domain_tools import _window_elements
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "QQ Music", "TXGuiFoundation", (125, 80, 1325, 880))
    elements = _window_elements(info, [{"ref": "e1", "role": "Button",
                                        "rect": [205, 415, 285, 455]}])
    assert elements[0]["rect"] == [80, 335, 160, 375]
    assert elements[0]["coordinate_space"] == "window"


def test_local_ocr_rect_does_not_include_window_origin(monkeypatch):
    import pytest
    pytest.skip("v0.8.0-beta.2: OCR shim uses new perception layer — migrate to test_perception.py")
    import numpy as np
    from reasonix_computer_use import text_vision
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(11, "QQ Music", "TXGuiFoundation", (125, 80, 1325, 880))
    image = np.zeros((800, 1200, 3), dtype=np.uint8)
    result = [([[80, 335], [144, 335], [144, 359], [80, 359]], "喜欢·48", 0.99)]
    monkeypatch.setattr(text_vision, "_capture_window", lambda *_a, **_k: (image, info))
    monkeypatch.setattr(text_vision.user32, "GetForegroundWindow", lambda: 11)
    monkeypatch.setattr(text_vision, "_ocr", lambda: lambda _image: (result, None))

    scanned = text_vision.scan_text("0xb")
    assert scanned["coordinate_space"] == "window"
    assert scanned["window"]["origin"] == [125, 80]
    assert scanned["matches"][0]["rect"] == [80, 335, 144, 359]


def test_targeted_ocr_runs_scaled_enhancement(monkeypatch):
    import pytest
    pytest.skip("v0.8.0-beta.2: OCR shim uses new perception layer — migrate to test_perception.py")
    from PIL import Image
    from reasonix_computer_use import text_vision
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(11, "Toolbar", "Canvas", (100, 200, 500, 500))
    image = Image.new("RGB", (400, 300), "white")
    calls = []

    def engine(array):
        calls.append(array.shape[:2])
        if len(calls) == 1:
            return [], None
        return [([[40, 60], [160, 60], [160, 100], [40, 100]], "设置", 0.91)], None

    monkeypatch.setattr(text_vision, "_capture_window", lambda *_a, **_k: (image, info))
    monkeypatch.setattr(text_vision.user32, "GetForegroundWindow", lambda: 11)
    monkeypatch.setattr(text_vision, "_ocr", lambda: engine)
    scanned = text_vision.scan_text("0xb", query="设置")
    assert calls == [(300, 400), (600, 800)]
    assert scanned["matches"][0]["rect"] == [20, 30, 80, 50]
    assert scanned["matches"][0]["enhanced"] is True


def test_ocr_merge_deduplicates_overlapping_text():
    import pytest
    pytest.skip("v0.8.0-beta.2: OCR shim uses new perception layer — migrate to test_perception.py")
    from reasonix_computer_use.text_vision import _merge_matches

    merged = _merge_matches([
        {"text": "设置", "confidence": 0.81, "rect": [10, 10, 50, 30], "enhanced": False},
        {"text": "设置", "confidence": 0.93, "rect": [11, 10, 51, 30], "enhanced": True},
    ])
    assert len(merged) == 1
    assert merged[0]["confidence"] == 0.93


def test_long_chinese_search_goal_keeps_relevant_ocr_text(monkeypatch):
    from reasonix_computer_use import domain_tools

    monkeypatch.setattr(domain_tools, "scan_text", lambda *_a, **_k: {
        "available": True,
        "matches": [
            {"text": "搜索音乐", "confidence": 0.99, "rect": [420, 30, 520, 65]},
            {"text": "本地和下载", "confidence": 0.98, "rect": [20, 100, 140, 130]},
        ],
    })

    elements, diagnostic = domain_tools._ocr_elements(
        "0x1", "定位搜索框或搜索入口以便查找周杰伦的枫", 40, "rapid")

    assert [item["name"] for item in elements] == ["搜索音乐"]
    assert diagnostic["recognized"] == 2
    assert diagnostic["relevant"] == 1


def test_ocr_does_not_offer_generic_play_control_for_named_song(monkeypatch):
    from reasonix_computer_use import domain_tools

    monkeypatch.setattr(domain_tools, "scan_text", lambda *_a, **_k: {
        "available": True,
        "matches": [
            {"text": "\u64ad\u653e", "confidence": 0.99, "rect": [10, 10, 40, 30]},
            {"text": "\u67ab", "confidence": 0.99, "rect": [80, 40, 120, 60]},
        ],
    })

    elements, diagnostic = domain_tools._ocr_elements(
        "0x1", "\u6253\u5f00QQ\u97f3\u4e50\uff0c\u5e76\u64ad\u653e\u5468\u6770\u4f26\u7684\u67ab", 40, "rapid")

    assert [item["name"] for item in elements] == ["\u67ab"]
    assert diagnostic["relevant"] == 1


def test_ocr_waits_for_search_result_text_to_stabilize(monkeypatch):
    from reasonix_computer_use import domain_tools

    scans = iter([
        {"available": True, "matches": [
            {"text": "\u67ab", "confidence": 0.99, "rect": [10, 10, 40, 30]}]},
        {"available": True, "matches": [
            {"text": "\u67ab", "confidence": 0.99, "rect": [10, 10, 40, 30]},
            {"text": "\u6674\u5929", "confidence": 0.99, "rect": [10, 40, 40, 60]}]},
        {"available": True, "matches": [
            {"text": "\u67ab", "confidence": 0.99, "rect": [10, 10, 40, 30]},
            {"text": "\u6674\u5929", "confidence": 0.99, "rect": [10, 40, 40, 60]},
            {"text": "\u9752\u82b1\u74f7", "confidence": 0.99, "rect": [10, 70, 60, 90]}]},
    ])
    monkeypatch.setattr(domain_tools, "scan_text", lambda *_a, **_k: next(scans))
    monkeypatch.setattr(domain_tools.time, "sleep", lambda *_a: None)

    elements, diagnostic = domain_tools._ocr_elements(
        "0x1", "\u9009\u62e9\u641c\u7d22\u7ed3\u679c\u4e2d\u5468\u6770\u4f26\u7684\u67ab", 40, "rapid")

    assert [item["name"] for item in elements] == ["\u67ab"]
    # Additional unrelated rows may render later, but the requested target is
    # already stable at the same position and will be re-located before click.
    assert diagnostic["stable"] is True


def test_ocr_rejects_target_that_keeps_moving_while_results_load(monkeypatch):
    from reasonix_computer_use import domain_tools

    scans = iter([
        {"available": True, "matches": [{"text": "枫", "confidence": 0.99,
                                           "rect": [10, 10, 40, 30]}]},
        {"available": True, "matches": [{"text": "枫", "confidence": 0.99,
                                           "rect": [10, 42, 40, 62]}]},
        {"available": True, "matches": [{"text": "枫", "confidence": 0.99,
                                           "rect": [10, 74, 40, 94]}]},
    ])
    monkeypatch.setattr(domain_tools, "scan_text", lambda *_a, **_k: next(scans))
    monkeypatch.setattr(domain_tools.time, "sleep", lambda *_a: None)

    _elements, diagnostic = domain_tools._ocr_elements(
        "0x1", "选择搜索结果中的枫", 40, "rapid")
    assert diagnostic["stable"] is False


def test_ocr_accepts_consecutive_settled_search_frames(monkeypatch):
    from reasonix_computer_use import domain_tools

    result_frame = {"available": True, "matches": [
        {"text": "枫", "confidence": 0.99, "rect": [10, 10, 40, 30]},
        {"text": "周杰伦", "confidence": 0.99, "rect": [50, 10, 110, 30]},
    ]}
    scans = iter([
        {"available": True, "matches": [{
            "text": "加载中", "confidence": 0.99, "rect": [10, 10, 60, 30],
        }]},
        result_frame,
        result_frame,
    ])
    monkeypatch.setattr(domain_tools, "scan_text", lambda *_a, **_k: next(scans))
    monkeypatch.setattr(domain_tools.time, "sleep", lambda *_a: None)

    elements, diagnostic = domain_tools._ocr_elements(
        "0x1", "选择搜索结果中周杰伦的枫", 40, "rapid")

    assert diagnostic["stable"] is True
    assert [item["name"] for item in elements] == ["枫"]


def test_named_find_goal_waits_for_results_without_explicit_list_word(monkeypatch):
    from reasonix_computer_use import domain_tools

    frame = {"available": True, "matches": [
        {"text": "枫", "confidence": 0.99, "rect": [10, 10, 40, 30]},
    ]}
    calls = []
    monkeypatch.setattr(domain_tools, "scan_text",
                        lambda *_a, **_k: calls.append(1) or frame)
    monkeypatch.setattr(domain_tools.time, "sleep", lambda *_a: None)

    elements, diagnostic = domain_tools._ocr_elements(
        "0x1", "查找周杰伦的枫", 40, "rapid")

    assert len(calls) == 3
    assert diagnostic["stable"] is True
    assert [item["name"] for item in elements] == ["枫"]


def test_locating_search_input_does_not_use_result_stability_wait(monkeypatch):
    from reasonix_computer_use import domain_tools

    calls = []
    monkeypatch.setattr(domain_tools, "scan_text", lambda *_a, **_k: calls.append(1) or {
        "available": True,
        "matches": [{"text": "搜索音乐", "confidence": 0.99,
                     "rect": [10, 10, 100, 30]}],
    })

    elements, diagnostic = domain_tools._ocr_elements(
        "0x1", "定位搜索框以便查找周杰伦的枫", 40, "rapid")

    assert len(calls) == 1
    assert diagnostic["stable"] is True
    assert [item["name"] for item in elements] == ["搜索音乐"]


def test_runtime_perception_survives_mcp_restart(monkeypatch, tmp_path):
    from reasonix_computer_use import runtime
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(0x123, "QQ Music", "TXGuiFoundation", (125, 80, 1325, 880),
                      77, r"E:\QQMusic\QQMusic.exe")
    monkeypatch.setenv("REASONIX_RUNTIME_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(runtime.user32, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(runtime, "get_window_info", lambda _hwnd: info)

    first_registry = runtime.WindowRegistry()
    first = first_registry.register(info, {"id": "qqmusic", "name": "QQ Music"})
    first.update({"texts": [("喜欢·48", [80, 335, 144, 359])]}, "ocr", [{
        "ref": "o12", "role": "text", "name": "喜欢·48", "value": "must-not-persist",
        "rect": [80, 335, 144, 359], "coordinate_space": "window",
    }])
    first.state_reads_without_action = 2
    first.task_goal_hash = "a" * 32
    first.task_goal_explicit = True
    first.completion_contract = {"playback": f"{'b' * 32}:1", "open": "required"}
    first.completed_intents = {"open"}
    first.completion_evidence = {"open": {
        "verified": True, "method": "window_activate", "effect": "应用已位于前台",
        "revision": first.revision,
    }}
    first.last_input_ref = "e-search"
    first.last_input_selector = {"role": "ComboBox", "automation_id": "SearchBox"}
    first.last_input_purpose = "search"
    first.last_input_revision = first.revision
    first.last_input_focus_hwnd = 0x456
    first.last_input_task_nonce = first.input_task_nonce
    first_registry.persist(first)

    restored = runtime.WindowRegistry().register(info, {"id": "qqmusic", "name": "QQ Music"})
    assert restored.restored is True
    assert restored.revision == first.revision
    assert restored.references["o12"]["name"] == "喜欢·48"
    assert restored.state_reads_without_action == 2
    assert restored.task_goal_hash == "a" * 32
    assert restored.completion_contract == first.completion_contract
    assert restored.completed_intents == {"open"}
    assert restored.completion_evidence["open"]["verified"] is True
    assert restored.last_input_ref == "e-search"
    assert restored.last_input_selector == {"role": "ComboBox", "automation_id": "SearchBox"}
    assert restored.last_input_focus_hwnd == 0x456
    assert restored.last_input_task_nonce == restored.input_task_nonce
    persisted = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
    assert "must-not-persist" not in persisted
    assert "播放周杰伦的枫" not in persisted


def test_send_unicode_text_rejects_silent_sendinput_failure(monkeypatch):
    from reasonix_computer_use import keyboard

    monkeypatch.setattr(keyboard, "_SendInput", lambda *_args: 0)
    with pytest.raises(OSError):
        keyboard.send_unicode_text("周")


def test_keyboard_virtual_key_is_not_sent_as_scan_code(monkeypatch):
    from reasonix_computer_use import keyboard

    calls = []
    monkeypatch.setattr(keyboard.ctypes.windll.user32, "keybd_event",
                        lambda *args: calls.append(args))
    keyboard._send_key(keyboard.VK_RETURN)
    assert calls[0][:2] == (keyboard.VK_RETURN, 0)


def test_modifier_aliases_are_normalized_for_shortcuts():
    from reasonix_computer_use import domain_tools

    key, modifiers = domain_tools._press_parts(["control", "shift", "A"])
    assert key == "A"
    assert modifiers == ["ctrl", "shift"]


@pytest.mark.asyncio
async def test_hold_key_supports_printable_character(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    sent = []
    monkeypatch.setattr(domain_tools, "_activate_for_keyboard", lambda _context: True)
    monkeypatch.setattr(domain_tools, "_resolve_action_vk", lambda key: 0x41 if key == "a" else None)
    monkeypatch.setattr(domain_tools, "_send_key",
                        lambda vk, key_up=False: sent.append((vk, key_up)))
    async def no_sleep(*_args, **_kwargs):
        return None
    monkeypatch.setattr(domain_tools.asyncio, "sleep", no_sleep)

    result = await domain_tools._execute(context, {"type": "hold_key", "key": "a", "duration": 0.1})
    assert result["status"] == "ok"
    assert sent == [(0x41, False), (0x41, True)]


def test_uia_walk_uses_created_true_condition(monkeypatch):
    import pytest
    pytest.skip("v0.8.0-beta.2: UIA shim uses new perception layer — migrate to test_perception.py")
    from reasonix_computer_use import ui_tree

    marker = object()
    calls = []

    class Children:
        Length = 0

    class Element:
        def FindAll(self, scope, condition):
            calls.append((scope, condition))
            return Children()

    class Automation:
        def CreateTrueCondition(self):
            return marker

    monkeypatch.setattr(ui_tree, "_uia", lambda: Automation())
    monkeypatch.setattr(ui_tree.comtypes.gen, "UIAutomationClient",
                        type("Constants", (), {"TreeScope_Descendants": 4}), raising=False)
    element = Element()
    assert list(ui_tree._walk(element)) == [(element, 0)]
    assert calls[0][1] is marker


@pytest.mark.asyncio
async def test_uia_state_never_captures_a_screenshot(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "QQ", "QQWindow", (0, 0, 400, 600), 10, "E:\\QQ\\QQ.exe")
    context = WindowContext("w1", 1)
    context.update({"title": "QQ"}, "window")
    context.info = lambda: info
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "observe", lambda *a, **k: {
        "elements": [{"ref": "e1", "role": "Button", "name": "设置", "rect": [1, 1, 20, 20],
                      "actions": ["invoke"]}]
    })
    monkeypatch.setattr(domain_tools, "window_payload", lambda *a, **k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "_capture_window",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("UIA path captured screenshot")))
    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "打开设置", "task_goal": "打开设置"}))
    assert result["source"] == "uia"
    assert result["elements"][0]["ref"] == "e1"

    monkeypatch.setattr(domain_tools, "observe",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("same revision re-queried UIA")))
    cached = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "打开设置", "since_revision": result["revision"]
    }))
    assert cached["unchanged"] is True
    assert cached["source"] == "uia"


@pytest.mark.asyncio
async def test_ocr_state_does_not_enter_visual_fallback(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "QQ", "QQWindow", (0, 0, 400, 600), 10, "E:\\QQ\\QQ.exe")
    context = WindowContext("w1", 1)
    context.update({"title": "QQ"}, "window")
    context.info = lambda: info
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "observe", lambda *a, **k: {"elements": []})
    monkeypatch.setattr(domain_tools, "_ocr_elements", lambda *a, **k: [
        {"ref": "o1", "role": "text", "name": "设置", "rect": [1, 1, 20, 20], "confidence": 0.99}
    ])
    monkeypatch.setattr(domain_tools, "window_payload", lambda *a, **k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "_capture_window",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("OCR path entered visual fallback")))
    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "打开设置", "task_goal": "打开设置"}))
    assert result["source"] == "ocr"


@pytest.mark.asyncio
async def test_unstable_ocr_search_results_are_not_clickable(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "QQ", "QQWindow", (0, 0, 400, 600), 10, "E:\\QQ\\QQ.exe")
    context = WindowContext("w1", 1)
    context.update({"title": "QQ"}, "window")
    context.info = lambda: info
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "observe", lambda *a, **k: {"elements": []})
    monkeypatch.setattr(domain_tools, "_ocr_elements", lambda *a, **k: ([
        {"ref": "o1", "role": "text", "name": "\u67ab", "rect": [1, 1, 20, 20], "confidence": 0.99}
    ], {"engine": "rapid", "stable": False, "relevant": 1}))
    monkeypatch.setattr(domain_tools, "window_payload", lambda *a, **k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "_capture_window",
                        lambda *a, **k: pytest.fail("unstable OCR must not enter visual fallback"))

    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "\u9009\u62e9\u641c\u7d22\u7ed3\u679c\u4e2d\u5468\u6770\u4f26\u7684\u67ab",
        "task_goal": "\u641c\u7d22\u5e76\u64ad\u653e\u5468\u6770\u4f26\u7684\u67ab", "mode": "ocr"}))

    assert result["source"] == "ocr"
    assert result["sufficient"] is False
    assert result["elements"] == []


@pytest.mark.asyncio
async def test_unstable_filtered_ocr_does_not_fall_through_to_other_channels(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "Music", "Custom", (0, 0, 400, 600), 10, "music.exe")
    context = WindowContext("w1", 1)
    context.update({"title": "Music"}, "window")
    context.info = lambda: info
    calls = []
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "observe", lambda *_a, **_k: {"elements": []})

    def ocr(*args, **_kwargs):
        calls.append(args[3])
        return [], {"engine": args[3], "stable": False, "recognized": 1, "relevant": 0}

    monkeypatch.setattr(domain_tools, "_ocr_elements", ocr)
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "_capture_window",
                        lambda *_a, **_k: pytest.fail("unstable OCR must not enter vision"))
    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "选择搜索结果中的目标歌曲",
        "task_goal": "搜索并播放目标歌曲", "mode": "auto",
    }))

    assert result["source"] == "ocr"
    assert result["sufficient"] is False
    assert result["blocked"] is False
    assert calls == ["rapid"]


@pytest.mark.asyncio
async def test_explicit_ocr_mode_bypasses_uia_and_visual(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "Custom", "CanvasWindow", (0, 0, 400, 600), 10, "E:\\Custom.exe")
    context = WindowContext("w1", 1)
    context.update({"title": "Custom"}, "window")
    context.info = lambda: info
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "observe",
                        lambda *_a, **_k: pytest.fail("OCR mode must bypass UIA"))
    monkeypatch.setattr(domain_tools, "_ocr_elements", lambda *_a, **_k: [{
        "ref": "o1", "role": "text", "name": "设置", "rect": [10, 20, 70, 45],
        "confidence": 0.99, "coordinate_space": "window",
    }])
    monkeypatch.setattr(domain_tools, "_capture_window",
                        lambda *_a, **_k: pytest.fail("OCR mode must not return visual"))
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})

    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "设置", "task_goal": "打开设置", "mode": "ocr"
    }))
    assert result["source"] == "ocr"


@pytest.mark.asyncio
async def test_explicit_uia_mode_bypasses_ocr_and_visual(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "Native", "NativeWindow", (0, 0, 400, 600), 10, "E:\\Native.exe")
    context = WindowContext("w1", 1)
    context.update({"title": "Native"}, "window")
    context.info = lambda: info
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "observe", lambda *_a, **_k: {"elements": [{
        "ref": "e1", "role": "Button", "name": "设置", "rect": [10, 20, 70, 45],
        "actions": ["invoke"],
    }]})
    monkeypatch.setattr(domain_tools, "_ocr_elements",
                        lambda *_a, **_k: pytest.fail("UIA mode must bypass OCR"))
    monkeypatch.setattr(domain_tools, "_capture_window",
                        lambda *_a, **_k: pytest.fail("UIA mode must not return visual"))
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})

    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "设置", "task_goal": "打开设置", "mode": "uia"
    }))
    assert result["source"] == "uia"


@pytest.mark.asyncio
async def test_mcp_state_attaches_only_returned_window_image(tmp_path, monkeypatch):
    from reasonix_computer_use import mcp_server
    from reasonix_computer_use import tools  # noqa: F401
    from reasonix_computer_use.mcp_server import TOOLS, handle_tools_call
    from reasonix_computer_use.vision_router import VisionRoute

    image = tmp_path / "window.png"
    image.write_bytes(b"png-data")

    async def handler(_args):
        return json.dumps({"status": "ok", "source": "visual", "image_path": str(image)})

    monkeypatch.setattr(mcp_server, "resolve_vision_route", lambda **_kwargs: VisionRoute(
        "native", True, "test", "host_declared_image_input"))
    monkeypatch.setitem(TOOLS["computer_state"], "handler", handler)
    result = await handle_tools_call(1, {
        "name": "computer_state", "arguments": {},
        "_reasonix_host_context": {"imageInputEnabled": True},
    })
    content = result["result"]["content"]
    assert [item["type"] for item in content] == ["text", "image"]


@pytest.mark.asyncio
async def test_visual_is_returned_once_per_revision(tmp_path, monkeypatch):
    from PIL import Image
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "Canvas", "CanvasWindow", (0, 0, 100, 100), 10, "E:\\Canvas.exe")
    context = WindowContext("w1", 1)
    context.update({"title": "Canvas"}, "window")
    context.info = lambda: info
    captures = []
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "observe", lambda *a, **k: {"elements": []})
    monkeypatch.setattr(domain_tools, "_ocr_elements", lambda *a, **k: [])
    monkeypatch.setattr(domain_tools, "window_payload", lambda *a, **k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "_get_screenshot_dir", lambda: str(tmp_path))

    def capture(*_args, **_kwargs):
        captures.append(1)
        return Image.new("RGB", (100, 100), "white"), info

    monkeypatch.setattr(domain_tools, "_capture_window", capture)
    first = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "图标", "task_goal": "点击无文字图标"}))
    second = json.loads(await domain_tools.computer_state({"window_id": "w1", "goal": "图标"}))
    third = json.loads(await domain_tools.computer_state({"window_id": "w1", "goal": "图标"}))
    assert first["source"] == "visual"
    assert second["source"] == "none"
    assert second["blocked"] is False
    assert second["recommended_mode"] == "uia"
    assert third["source"] == "none"
    assert third["blocked"] is True
    assert third["recommended_mode"] == "uia"
    assert context.hard_blocked is True
    assert len(captures) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["uia", "ocr"])
async def test_visual_budget_does_not_block_structured_recovery(monkeypatch, mode):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "Settings", "Native", (0, 0, 400, 300), 10, "settings.exe")
    context = WindowContext("w1", 1)
    context.update({"title": "Settings"}, "window")
    context.visual_count = 2
    context.strategy_level = 3
    context.info = lambda: info
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "_capture_window",
                        lambda *_a, **_k: pytest.fail("structured recovery must not capture"))
    if mode == "uia":
        monkeypatch.setattr(domain_tools, "observe", lambda *_a, **_k: {"elements": [{
            "ref": "e1", "role": "Button", "name": "Settings", "rect": [5, 5, 50, 25],
        }]})
        monkeypatch.setattr(domain_tools, "_ocr_elements",
                            lambda *_a, **_k: pytest.fail("explicit UIA must not OCR"))
    else:
        monkeypatch.setattr(domain_tools, "observe",
                            lambda *_a, **_k: pytest.fail("explicit OCR must not use UIA"))
        monkeypatch.setattr(domain_tools, "_ocr_elements", lambda *_a, **_k: ([{
            "ref": "o1", "role": "text", "name": "Settings", "rect": [5, 5, 50, 25],
            "confidence": 0.99, "coordinate_space": "window",
        }], {"engine": "rapid", "stable": True, "relevant": 1}))

    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "Settings", "task_goal": "Open Settings", "mode": mode,
    }))
    assert result["source"] == mode
    assert result["sufficient"] is True
    assert result["blocked"] is False


def test_begin_task_resets_all_visual_deduplication_state():
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    old_nonce = context.input_task_nonce
    context.visual_count = 2
    context.visual_sent_for_revision = "r9-old"
    context.image_hash = "old-image"
    context.visual_rect = (1, 2, 3, 4)
    context.visual_no_progress_count = 1
    context.stale_revision_count = 2
    context.task_goal = "播放周杰伦的枫"
    context.task_goal_hash = "a" * 32
    context.task_goal_explicit = True
    context.observation_goal = "确认搜索结果"
    context.completion_contract = {"playback": f"{'b' * 32}:1"}
    context.restored = True
    context.begin_task()

    assert context.visual_count == 0
    assert context.visual_sent_for_revision == ""
    assert context.image_hash == ""
    assert context.visual_rect is None
    assert context.visual_no_progress_count == 0
    assert context.stale_revision_count == 0
    assert context.task_goal == ""
    assert context.task_goal_hash == ""
    assert context.task_goal_explicit is False
    assert context.observation_goal == ""
    assert context.completion_contract == {}
    assert context.completed_intents == set()
    assert context.completion_evidence == {}
    assert context.restored is False
    assert context.input_task_nonce != old_nonce


@pytest.mark.asyncio
async def test_explicit_visual_mode_bypasses_uia_and_ocr(tmp_path, monkeypatch):
    from PIL import Image
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "Canvas", "CanvasWindow", (20, 30, 220, 180), 10, "E:\\Canvas.exe")
    context = WindowContext("w1", 1)
    context.update({"title": "Canvas"}, "window")
    context.info = lambda: info
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "observe",
                        lambda *_a, **_k: pytest.fail("visual mode must bypass UIA"))
    monkeypatch.setattr(domain_tools, "_ocr_elements",
                        lambda *_a, **_k: pytest.fail("visual mode must bypass OCR"))
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "_get_screenshot_dir", lambda: str(tmp_path))
    monkeypatch.setattr(domain_tools, "_capture_window",
                        lambda *_a, **_k: (Image.new("RGB", (200, 150), "white"), info))

    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "无文字图标", "task_goal": "点击无文字图标", "mode": "visual"
    }))
    assert result["source"] == "visual"
    assert result["origin"] == [20, 30]


@pytest.mark.asyncio
async def test_invalid_state_mode_is_rejected_before_perception(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _window_id: context)
    monkeypatch.setattr(
        domain_tools, "observe",
        lambda *_a, **_k: pytest.fail("invalid mode must not run perception"),
    )
    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "读取状态", "task_goal": "读取状态", "mode": "memory",
    }))
    assert result["status"] == "error"
    assert result["code"] == "invalid_state_mode"
    assert result["recommended_mode"] == "auto"


@pytest.mark.asyncio
async def test_visual_remains_reachable_after_uia_and_ocr_failures(tmp_path, monkeypatch):
    from PIL import Image
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "Canvas", "CanvasWindow", (0, 0, 100, 100), 10, "E:\\Canvas.exe")
    context = WindowContext("w1", 1)
    context.update({"title": "Canvas"}, "window")
    context.info = lambda: info
    for _ in range(4):
        context.fail()
    assert context.strategy_level == 3
    assert context.hard_blocked is False

    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "observe",
                        lambda *_a, **_k: pytest.fail("visual strategy must skip UIA"))
    monkeypatch.setattr(domain_tools, "_ocr_elements",
                        lambda *_a, **_k: pytest.fail("visual strategy must skip OCR"))
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "_get_screenshot_dir", lambda: str(tmp_path))
    monkeypatch.setattr(domain_tools, "_capture_window",
                        lambda *_a, **_k: (Image.new("RGB", (100, 100), "black"), info))

    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "图标", "task_goal": "点击图标"}))
    assert result["source"] == "visual"
    assert result["blocked"] is False


def test_profile_and_index_are_replaced_together(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONIX_MEMORY_DIR", str(tmp_path))
    from reasonix_computer_use.system_profile import read_index, read_profile, write_profile_and_index

    index = {
        "schema_version": 2, "updated_at": "now", "reason": "test",
        "system": {"platform": "Windows", "architecture": "AMD64", "language": "zh_CN",
                   "timezone": "CST", "dpi_awareness": "per-monitor-v2"},
        "hardware": {"cpu": "cpu", "gpu": "gpu", "memory_gb": 16},
        "displays": [], "known_folders": {"桌面": {"path": "F:\\Desktop"}},
        "applications": [{"name": "QQ", "path": "E:\\QQ\\QQ.exe"}],
        "quick_scan_complete": True, "enrichment_complete": False,
    }
    write_profile_and_index(index)
    assert read_index()["known_folders"]["桌面"]["path"] == "F:\\Desktop"
    profile = read_profile()
    assert "F:\\Desktop" in profile
    assert "E:\\QQ\\QQ.exe" not in profile


def test_app_search_prefers_exact_launchable_match(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONIX_MEMORY_DIR", str(tmp_path))
    from reasonix_computer_use.system_profile import write_profile_and_index
    from reasonix_computer_use.system_index import search_apps

    index = {
        "schema_version": 2, "applications": [
            {"id": "music", "name": "QQ音乐", "path": "E:\\QQMusic\\QQMusic.exe", "confidence": 1},
            {"id": "qq", "name": "QQ", "path": "E:\\QQ\\QQ.exe", "confidence": 1},
        ]
    }
    write_profile_and_index(index)
    assert search_apps("QQ", refresh_on_miss=False)[0]["id"] == "qq"


def test_localized_windows_app_alias(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONIX_MEMORY_DIR", str(tmp_path))
    from reasonix_computer_use.system_profile import write_profile_and_index
    from reasonix_computer_use.system_index import search_apps

    write_profile_and_index({"schema_version": 2, "applications": [
        {"id": "notepad", "name": "notepad", "path": "C:\\Windows\\notepad.exe", "confidence": 1}
    ]})
    assert search_apps("记事本", refresh_on_miss=False)[0]["id"] == "notepad"


def test_calculator_miss_queries_start_apps_before_full_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONIX_MEMORY_DIR", str(tmp_path))
    from reasonix_computer_use.system_profile import write_profile_and_index
    from reasonix_computer_use import system_index

    write_profile_and_index({"schema_version": 2, "applications": [
        {"id": "nvidia-calc", "name": "Occupancy Calculator", "path": "", "confidence": 0.8}
    ]})
    calculator = {"id": "calc", "name": "计算器",
                  "path": r"shell:AppsFolder\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
                  "launch_target": r"shell:AppsFolder\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
                  "source": "start-apps", "confidence": 0.9}
    monkeypatch.setattr(system_index, "_scan_start_apps", lambda: [calculator])
    monkeypatch.setattr(system_index, "build_index",
                        lambda *_a, **_k: pytest.fail("full index refresh should not run"))
    assert system_index.search_apps("Calculator")[0]["id"] == "calc"


def test_strong_app_match_rejects_desktop_substring():
    from reasonix_computer_use.system_index import is_strong_app_match

    remote = {"name": "远程桌面连接", "path": "shell:AppsFolder\\RemoteDesktop"}
    calculator = {"name": "计算器", "path": "shell:AppsFolder\\Calculator"}
    assert is_strong_app_match("桌面", remote) is False
    assert is_strong_app_match("Calculator", calculator) is True


def test_start_apps_uses_utf8_for_localized_names(monkeypatch):
    from reasonix_computer_use import system_index

    class Completed:
        stdout = '[{"Name":"计算器","AppID":"Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"}]'

    calls = []
    monkeypatch.setattr(system_index.subprocess, "run",
                        lambda command, **kwargs: calls.append((command, kwargs)) or Completed())
    apps = system_index._scan_start_apps()
    assert apps[0]["name"] == "计算器"
    assert "OutputEncoding" in calls[0][0][-1]
    assert calls[0][1]["encoding"] == "utf-8"


def test_uninstaller_is_not_a_launch_target(tmp_path):
    from reasonix_computer_use.system_index import _launchable_executable

    uninstaller = tmp_path / "unins000.exe"
    uninstaller.touch()
    app = tmp_path / "Ollama App.exe"
    app.touch()
    assert _launchable_executable(str(uninstaller)) is False
    assert _launchable_executable(str(app)) is True


def test_launch_uses_wmi_broker(monkeypatch, tmp_path):
    from reasonix_computer_use import domain_tools

    executable = tmp_path / "app.exe"
    executable.touch()
    calls = []
    monkeypatch.setattr(domain_tools, "launch_via_system_broker",
                        lambda target, args, cwd: calls.append((target, args, cwd)) or (1234, "wmi"))
    pid, method = domain_tools._launch({"path": str(executable)})
    assert pid == 1234
    assert method == "wmi"
    assert calls == [(str(executable), "", str(executable.parent))]


def test_launch_shell_app_uses_wmi_explorer(monkeypatch):
    from reasonix_computer_use import domain_tools

    launched = []
    monkeypatch.setattr(domain_tools, "shell_execute", lambda target: launched.append(target))
    pid, method = domain_tools._launch({"path": r"shell:AppsFolder\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"})
    assert (pid, method) == (0, "wmi-explorer")
    assert launched == [r"shell:AppsFolder\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"]


def test_launch_broker_passes_nonempty_arguments(monkeypatch, tmp_path):
    from reasonix_computer_use import domain_tools

    executable = tmp_path / "app.exe"
    executable.touch()
    launched = []
    monkeypatch.setattr(domain_tools, "launch_via_system_broker",
                        lambda target, args, cwd: launched.append((target, args, cwd)) or (123, "wmi"))
    domain_tools._launch({"path": str(executable), "launch_args": "--new-window"})
    assert launched[0][1] == "--new-window"


def test_wmi_broker_uses_fixed_script_and_environment(monkeypatch):
    from reasonix_computer_use import process_broker

    calls = []

    class Completed:
        returncode = 0
        stdout = "4321"
        stderr = ""

    monkeypatch.setattr(process_broker.subprocess, "run",
                        lambda command, **kwargs: calls.append((command, kwargs)) or Completed())
    pid, method = process_broker.launch_via_system_broker(
        r"C:\Apps\app.exe", '--name "value"', r"C:\Apps")
    assert (pid, method) == (4321, "wmi")
    assert calls[0][1]["env"]["REASONIX_BROKER_COMMAND"] == 'C:\\Apps\\app.exe --name "value"'
    assert "C:\\Apps\\app.exe" not in " ".join(calls[0][0])


def test_wmi_broker_rejects_failed_creation(monkeypatch):
    from reasonix_computer_use import process_broker

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "access denied"

    monkeypatch.setattr(process_broker.subprocess, "run", lambda *_a, **_k: Completed())
    with pytest.raises(process_broker.LaunchBrokerError, match="access denied"):
        process_broker.launch_via_system_broker(r"C:\Apps\app.exe")


def test_edge_components_are_not_application_candidates():
    from reasonix_computer_use.system_index import _is_non_app_name

    assert _is_non_app_name("Microsoft Edge Update")
    assert _is_non_app_name("Microsoft Edge WebView2 Runtime")
    assert not _is_non_app_name("Microsoft Edge")


def test_pillow_capture_uses_physical_bbox(monkeypatch):
    from PIL import ImageGrab
    from reasonix_computer_use.screenshot import _grab_region

    calls = []
    monkeypatch.setattr(ImageGrab, "grab", lambda **kwargs: calls.append(kwargs) or object())
    _grab_region(-100, 25, 640, 480)
    assert calls == [{"bbox": (-100, 25, 540, 505), "all_screens": True}]


def test_window_capture_returns_the_exact_physical_grab_rect(monkeypatch):
    from contextlib import nullcontext
    from reasonix_computer_use import screenshot
    from reasonix_computer_use.windows import WindowInfo

    original = WindowInfo(0x123, "Moving", "Window", (100, 100, 500, 400),
                          77, r"C:\Apps\moving.exe")
    calls = []
    monkeypatch.setattr(screenshot, "resolve_window", lambda _window_id: original)
    monkeypatch.setattr(screenshot, "get_window_rect", lambda _hwnd: (125, 90, 545, 410))
    monkeypatch.setattr(screenshot, "physical_pixel_context", nullcontext)
    monkeypatch.setattr(
        screenshot, "_grab_region",
        lambda left, top, width, height: calls.append((left, top, width, height)) or object(),
    )

    _image, captured = screenshot._capture_window("0x123", activate=False)

    assert calls == [(125, 90, 420, 320)]
    assert captured.rect == (125, 90, 545, 410)
    assert (captured.hwnd, captured.pid, captured.process_path) == (
        original.hwnd, original.pid, original.process_path)


@pytest.mark.asyncio
async def test_missing_environment_blocks_app_before_launch(monkeypatch):
    from reasonix_computer_use import domain_tools

    monkeypatch.setattr(domain_tools, "environment_status",
                        lambda: {"ready": False, "missing": ["PIL"]})
    monkeypatch.setattr(domain_tools, "search_apps",
                        lambda *_args, **_kwargs: pytest.fail("application search must not run"))
    result = json.loads(await domain_tools.computer_app({"operation": "launch", "query": "QQ"}))
    assert result["status"] == "setup_required"
    assert result["blocked"] is True
    assert result["recommended_tool"] == "computer_system"


@pytest.mark.asyncio
async def test_app_search_returns_explicit_candidates(monkeypatch):
    from reasonix_computer_use import domain_tools

    monkeypatch.setattr(domain_tools, "search_apps", lambda query, limit=10: [
        {"id": "qq", "name": "QQ", "path": "E:\\QQ\\QQ.exe", "launch_target": "E:\\QQ\\QQ.exe",
         "source": "shortcut", "confidence": 1.0}
    ])
    result = json.loads(await domain_tools.computer_app({"operation": "search", "query": "QQ"}))
    assert result["matches"][0]["path"] == "E:\\QQ\\QQ.exe"
    assert "app_id" in result["next_hint"]


@pytest.mark.asyncio
async def test_launch_treats_unknown_app_id_as_query(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    app = {"id": "real-id", "name": "notepad", "path": "C:\\notepad.exe", "confidence": 1}
    info = WindowInfo(1, "记事本", "Notepad", (0, 0, 400, 400), 10, "C:\\notepad.exe")
    context = WindowContext("w1", 1, app_id="real-id", app_name="notepad")
    monkeypatch.setattr(domain_tools, "find_app", lambda _app_id: None)
    monkeypatch.setattr(domain_tools, "search_apps", lambda query, limit=10: [app])
    monkeypatch.setattr(domain_tools, "_find_app_window", lambda *a, **k: info)
    monkeypatch.setattr(domain_tools.REGISTRY, "register", lambda *a, **k: context)
    monkeypatch.setattr(domain_tools, "_prime_window_state", lambda *a, **k: None)
    monkeypatch.setattr(domain_tools, "activate_window", lambda *a, **k: True)
    monkeypatch.setattr(domain_tools, "window_payload", lambda *a, **k: {"id": "w1"})
    result = json.loads(await domain_tools.computer_app({"operation": "launch", "app_id": "notepad"}))
    assert result["status"] == "ok"
    assert result["app"]["id"] == "real-id"
    assert result["recommended_tool"] == "computer_state"
    assert "task_goal" in result["required_next_args"]


@pytest.mark.asyncio
async def test_launch_broker_failure_is_blocking(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.process_broker import LaunchBrokerError

    app = {"id": "app", "name": "App", "path": r"C:\App\app.exe",
           "launch_target": r"C:\App\app.exe"}
    monkeypatch.setattr(domain_tools, "environment_status", lambda: {"ready": True})
    monkeypatch.setattr(domain_tools, "find_app", lambda _app_id: app)
    monkeypatch.setattr(domain_tools, "_find_app_window", lambda *_a, **_k: None)
    monkeypatch.setattr(domain_tools, "_launch",
                        lambda _app: (_ for _ in ()).throw(LaunchBrokerError("WMI unavailable")))
    result = json.loads(await domain_tools.computer_app({"operation": "launch", "app_id": "app"}))
    assert result["code"] == "launch_isolation_failed"
    assert result["retryable"] is False
    assert result["blocked"] is True


@pytest.mark.asyncio
async def test_open_file_returns_tracked_window(monkeypatch, tmp_path):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    document = tmp_path / "data.xlsx"
    document.write_bytes(b"xlsx")
    info = WindowInfo(2, "data.xlsx - Excel", "XLMAIN", (0, 0, 800, 600), 22,
                      r"C:\Office\EXCEL.EXE")
    context = WindowContext("w1", 2, app_name="Excel", owner_pid=22)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [info])
    monkeypatch.setattr(domain_tools, "shell_execute", lambda _path: 123)
    monkeypatch.setattr(domain_tools.REGISTRY, "register", lambda *_a, **_k: context)
    monkeypatch.setattr(domain_tools, "_prime_window_state", lambda *_a, **_k: None)
    monkeypatch.setattr(domain_tools, "activate_window", lambda *a, **k: True)
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    result = json.loads(await domain_tools.computer_app({"operation": "open_file",
                                                          "path": str(document)}))
    assert result["status"] == "ok"
    assert result["window"]["id"] == "w1"


@pytest.mark.asyncio
async def test_open_path_resolves_known_folder_and_verifies_foreground(monkeypatch, tmp_path):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(3, tmp_path.name, "CabinetWClass", (0, 0, 800, 600), 33,
                      r"C:\Windows\explorer.exe")
    context = WindowContext("w3", 3, owner_pid=33)
    monkeypatch.setattr(domain_tools, "_known_folder_path", lambda value: tmp_path if value == "桌面" else None)
    monkeypatch.setattr(domain_tools, "_known_folder_mismatch", lambda _path: "")
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [info])
    monkeypatch.setattr(domain_tools, "shell_execute", lambda _path: 123)
    monkeypatch.setattr(domain_tools.REGISTRY, "register", lambda *_a, **_k: context)
    monkeypatch.setattr(domain_tools, "_prime_window_state", lambda *_a, **_k: None)
    monkeypatch.setattr(domain_tools, "activate_window", lambda *a, **k: True)
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w3"})
    result = json.loads(await domain_tools.computer_app({"operation": "open_path", "path": "桌面"}))
    assert result["status"] == "ok"
    assert result["foreground_verified"] is True
    assert result["path"] == str(tmp_path)


@pytest.mark.asyncio
async def test_launch_foreground_failure_is_terminal(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    app = {"id": "app", "name": "App", "path": r"C:\App.exe", "confidence": 1}
    info = WindowInfo(4, "App", "AppWindow", (0, 0, 400, 300), 44, r"C:\App.exe")
    monkeypatch.setattr(domain_tools, "search_apps", lambda *a, **k: [app])
    monkeypatch.setattr(domain_tools, "_find_app_window", lambda *a, **k: info)
    monkeypatch.setattr(domain_tools.REGISTRY, "register", lambda *a, **k: WindowContext("w4", 4))
    monkeypatch.setattr(domain_tools, "activate_window", lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))
    result = json.loads(await domain_tools.computer_app({"operation": "launch", "query": "App"}))
    assert result["code"] == "foreground_not_acquired"
    assert result["blocked"] is True
    assert result["retryable"] is False


@pytest.mark.asyncio
async def test_close_rejects_task_external_window(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    owned = WindowInfo(5, "Owned", "Owned", (0, 0, 300, 200), 50, r"C:\Owned.exe")
    foreign = WindowInfo(5, "Foreign", "Foreign", (0, 0, 300, 200), 99, r"C:\Foreign.exe")
    context = WindowContext("w5", 5, app_path=owned.process_path, owner_pid=50)
    context.info = lambda: owned
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "get_window_info", lambda _: foreign)
    result = json.loads(await domain_tools.computer_app({"operation": "close", "window_id": "w5"}))
    assert result["code"] == "window_ownership_mismatch"
    assert result["blocked"] is True


@pytest.mark.asyncio
async def test_close_requires_window_to_actually_disappear(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(5, "Owned", "Owned", (0, 0, 300, 200), 50, r"C:\Owned.exe")
    context = WindowContext("w5", 5, app_path=info.process_path, owner_pid=50)
    context.info = lambda: info
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "get_window_info", lambda _: info)
    monkeypatch.setattr(domain_tools.user32, "PostMessageW", lambda *_a: 1)
    monkeypatch.setattr(domain_tools.user32, "IsWindow", lambda _hwnd: True)
    async def no_sleep(*_args, **_kwargs):
        return None
    monkeypatch.setattr(domain_tools.asyncio, "sleep", no_sleep)
    ticks = iter([0.0, 3.0])
    monkeypatch.setattr(domain_tools.time, "monotonic", lambda: next(ticks, 3.0))

    result = json.loads(await domain_tools.computer_app({"operation": "close", "window_id": "w5"}))
    assert result["code"] == "close_not_verified"
    assert result["evidence"]["window_closed"] is False


@pytest.mark.asyncio
async def test_close_returns_success_only_after_window_disappears(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(6, "Owned", "Owned", (0, 0, 300, 200), 60, r"C:\Owned.exe")
    context = WindowContext("w6", 6, app_path=info.process_path, owner_pid=60)
    context.info = lambda: info
    states = iter([True, False, False])
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "get_window_info", lambda _: info)
    monkeypatch.setattr(domain_tools.user32, "PostMessageW", lambda *_a: 1)
    monkeypatch.setattr(domain_tools.user32, "IsWindow", lambda _hwnd: next(states, False))
    async def no_sleep(*_args, **_kwargs):
        return None
    monkeypatch.setattr(domain_tools.asyncio, "sleep", no_sleep)

    result = json.loads(await domain_tools.computer_app({"operation": "close", "window_id": "w6"}))
    assert result["status"] == "ok"
    assert result["verified"] is True
    assert result["evidence"]["window_closed"] is True


def test_guessed_default_desktop_is_rejected_when_redirected(monkeypatch):
    from reasonix_computer_use import domain_tools

    monkeypatch.setenv("USERPROFILE", r"C:\Users\Tester")
    monkeypatch.setattr(domain_tools, "ensure_index", lambda: {
        "known_folders": {"桌面": {"path": r"F:\Desktop"}}})
    message = domain_tools._known_folder_mismatch(Path(r"C:\Users\Tester\Desktop\report.xlsx"))
    assert "F:\\Desktop" in message


@pytest.mark.asyncio
async def test_action_rejects_stale_revision(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "A"}, "uia")
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": "old", "actions": [{"type": "wait", "seconds": 0}]
    }))
    assert result["code"] == "stale_revision"


@pytest.mark.asyncio
async def test_second_stale_revision_persists_hard_block(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w-stale", 1)
    context.update({"title": "A"}, "uia")
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    for _ in range(2):
        result = json.loads(await domain_tools.computer_action({
            "window_id": "w-stale", "revision": "old",
            "actions": [{"type": "wait", "seconds": 0}],
        }))
    assert result["code"] == "stale_revision"
    assert result["blocked"] is True
    assert context.hard_blocked is True
    stopped = json.loads(await domain_tools.computer_action({
        "window_id": "w-stale", "revision": context.revision,
        "actions": [{"type": "wait", "seconds": 0}],
    }))
    assert stopped["code"] == "execution_blocked"


@pytest.mark.asyncio
async def test_structured_state_rejects_bare_pixel_coordinates(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "A"}, "uia", [{"ref": "e1", "role": "Button", "name": "确认"}])
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "click_point", "x": 20, "y": 30}],
        "expect": {"text_present": "完成"},
    }))
    assert result["code"] == "coordinate_requires_visual"


@pytest.mark.asyncio
async def test_action_rejects_required_method_substitution(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "Desktop"}, "uia")
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "required_method": ["right_click"],
        "actions": [{"type": "press", "keys": ["SHIFT", "F10"]}],
    }))
    assert result["code"] == "required_method_missing"
    assert result["blocked"] is True


def test_exact_value_and_playback_require_semantic_evidence(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "Calculator", "Calc", (0, 0, 400, 500), 1, "calc.exe")
    context.elements = [{"name": "401", "role": "Text"}, {"name": "周杰伦", "selected": True}]
    monkeypatch.setattr(domain_tools, "find_text", lambda *a, **k: {"matches": []})
    assert domain_tools._verify(context, {"value_equals": "210"}, True)["verified"] is False
    assert domain_tools._verify(context, {"playback_active": True}, True)["verified"] is False
    baseline = domain_tools._verification_baseline(context, {"playback_active": True})
    context.elements.append({"name": "暂停", "role": "Button"})
    assert domain_tools._verify(
        context, {"playback_active": True}, True, baseline=baseline)["verified"] is True


@pytest.mark.asyncio
async def test_unrelated_ocr_does_not_block_visual(monkeypatch, tmp_path):
    from PIL import Image
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(8, "Canvas", "Custom", (10, 20, 210, 220), 80, "canvas.exe")
    context = WindowContext("w8", 8)
    context.update({"title": "Canvas"}, "window")
    context.info = lambda: info
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "observe", lambda *a, **k: {"elements": []})
    monkeypatch.setattr(domain_tools, "_ocr_elements", lambda *a, **k: ([], {
        "engine": a[3] if len(a) > 3 else "rapid", "recognized": 12, "relevant": 0}))
    monkeypatch.setattr(domain_tools, "_capture_window", lambda *a, **k: (Image.new("RGB", (200, 200), "white"), info))
    monkeypatch.setattr(domain_tools, "_get_screenshot_dir", lambda: str(tmp_path))
    monkeypatch.setattr(domain_tools, "window_payload", lambda *a, **k: {"id": "w8"})
    result = json.loads(await domain_tools.computer_state({
        "window_id": "w8", "goal": "点击无文字图标", "task_goal": "点击无文字图标"}))
    assert result["source"] == "visual"
    assert result["sufficient"] is True


def test_windows_ocr_missing_language_bridge_is_soft_failure(monkeypatch):
    import pytest
    pytest.skip("v0.8.0-beta.2: OCR shim uses new perception layer — migrate to test_perception.py")
    from reasonix_computer_use import text_vision

    result = text_vision.scan_text_windows("0x1", query="设置")
    assert result["matches"] == []
    assert result["available"] is False


def test_desktop_digest_ignores_taskbar_animation():
    from PIL import Image
    from reasonix_computer_use.domain_tools import _image_digest

    first = Image.new("RGB", (300, 200), "white")
    second = first.copy()
    for x in range(240, 300):
        for y in range(150, 200):
            second.putpixel((x, y), (0, 0, 0))
    assert _image_digest(first, True) == _image_digest(second, True)
    assert _image_digest(first, False) != _image_digest(second, False)


@pytest.mark.asyncio
async def test_action_blocks_sensitive_text_before_execution(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "登录"}, "uia")
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "type", "purpose": "输入密码", "text": "secret"}]
    }))
    assert result["code"] == "confirmation_required"


@pytest.mark.asyncio
async def test_action_stops_batch_on_first_failure(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "A"}, "uia")
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    calls = []

    async def fail(_context, action):
        calls.append(action)
        return {"status": "error", "code": "failed"}

    monkeypatch.setattr(domain_tools, "_execute", fail)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "wait"}, {"type": "press", "keys": ["ENTER"]}]
    }))
    assert result["code"] == "batch_stopped"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_batch_stops_when_action_replaces_target_window(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1, owner_pid=10)
    context.update({"title": "Login"}, "uia", [{"ref": "e1", "role": "Button", "name": "Next"}])
    replacement = WindowInfo(2, "Main", "Main", (0, 0, 800, 600), 10, "app.exe")
    calls = []
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [])
    monkeypatch.setattr(domain_tools.user32, "IsWindow", lambda _hwnd: False)

    async def execute(_context, action):
        calls.append(action["type"])
        return {"status": "ok"}

    monkeypatch.setattr(domain_tools, "_execute", execute)
    monkeypatch.setattr(domain_tools, "_adopt_new_window",
                        lambda current, _handles: current.adopt_window(replacement))
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "click_ref", "ref": "e1"},
                    {"type": "press", "keys": ["ENTER"]}],
    }))
    assert result["code"] == "window_revision_changed"
    assert calls == ["click_ref"]
    assert result["revision"] == context.revision


@pytest.mark.asyncio
async def test_pointer_action_rechecks_foreground_before_injection(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 101)
    context.update({"title": "A"}, "uia", [{"ref": "e1", "role": "Button", "name": "确认"}])
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [])
    monkeypatch.setattr(domain_tools.user32, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(domain_tools, "activate_window",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("denied")))
    monkeypatch.setattr(domain_tools, "_execute",
                        lambda *_a, **_k: pytest.fail("unfocused pointer action must not execute"))

    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "click_ref", "ref": "e1"}],
    }))
    assert result["code"] == "foreground_not_acquired"
    assert result["blocked"] is True


@pytest.mark.asyncio
async def test_action_accepts_legacy_click_shape_once(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.update({"title": "A"}, "uia", [{"ref": "e1", "role": "Button", "name": "搜索"}])
    context.info = lambda: WindowInfo(1, "A", "A", (0, 0, 500, 500))
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [])
    monkeypatch.setattr(domain_tools, "_wait_stable", lambda *_a, **_k: asyncio.sleep(0))
    monkeypatch.setattr(domain_tools, "_refresh_semantic", lambda *_a: True)
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "remember_success", lambda *_a: None)
    seen = []

    async def execute(_context, action):
        seen.append(action)
        return {"status": "ok"}

    monkeypatch.setattr(domain_tools, "_execute", execute)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"action": "click", "ref": "e1"}]
    }))
    assert result["status"] == "ok"
    assert seen[0]["type"] == "click_ref"


@pytest.mark.asyncio
async def test_input_like_combobox_is_focused_instead_of_expanded(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.elements = [{"ref": "e1", "role": "ComboBox", "name": "Send a message",
                         "actions": ["focus", "click", "set_value", "expand"]}]
    calls = []

    async def act(args):
        calls.append(args)
        return json.dumps({"status": "ok"})

    monkeypatch.setattr(domain_tools, "uia_act", act)
    result = await domain_tools._click_ref(context, "e1")
    assert result["status"] == "ok"
    assert calls[0]["action"] == "focus"


@pytest.mark.asyncio
async def test_uia_ref_is_relocated_after_mcp_registry_restart(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "Settings", "AppWindow", (100, 200, 900, 800))
    context = WindowContext("w1", 1, restored=True)
    context.info = lambda: info
    context.elements = [{"ref": "e4", "role": "Button", "name": "设置",
                         "id": "settings", "rect": [40, 50, 120, 90],
                         "coordinate_space": "window", "actions": ["invoke"]}]
    calls = []

    async def act(args):
        calls.append(args)
        if len(calls) == 1:
            return json.dumps({"status": "error", "code": "stale_ref"})
        return json.dumps({"status": "ok"})

    monkeypatch.setattr(domain_tools, "uia_act", act)
    monkeypatch.setattr(domain_tools, "observe", lambda *_a, **_k: {"elements": [{
        "ref": "e9", "role": "Button", "name": "设置", "id": "settings",
        "rect": [140, 250, 220, 290], "actions": ["invoke"],
    }]})
    result = await domain_tools._click_ref(context, "e4")
    assert result["status"] == "ok"
    assert [item["ref"] for item in calls] == ["e4", "e9"]
    assert context.elements[0]["rect"] == [40, 50, 120, 90]


@pytest.mark.asyncio
async def test_ocr_ref_clicks_its_own_rectangle(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "Browser", "WebView", (500, 300, 1300, 900))
    context.elements = [{"ref": "o15", "role": "text", "name": "百度",
                         "rect": [100, 200, 300, 240]}]
    clicks = []

    async def click(args):
        clicks.append(args)
        return json.dumps({"status": "ok"})

    monkeypatch.setattr(domain_tools, "find_text", lambda *_a, **_k: {
        "matches": [{"text": "百度", "confidence": 0.99, "rect": [100, 200, 300, 240]}]
    })
    monkeypatch.setattr(domain_tools, "computer_mouse_click", click)
    result = await domain_tools._execute(context, {"type": "click_text", "ref": "o15"})
    assert result["status"] == "ok"
    assert clicks[0]["x"] == 700
    assert clicks[0]["y"] == 520


@pytest.mark.asyncio
async def test_empty_click_text_is_rejected_without_ocr(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    monkeypatch.setattr(domain_tools, "find_text",
                        lambda *_a, **_k: pytest.fail("empty OCR query must not run"))
    result = await domain_tools._execute(WindowContext("w1", 1),
                                         {"type": "click_text", "text": ""})
    assert result["code"] == "missing_text"


@pytest.mark.asyncio
async def test_link_like_edit_uses_physical_click(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "Browser", "WebView", (50, 100, 900, 700))
    context.elements = [{"ref": "e1", "role": "Edit", "name": "DeepSeek | 深度求索",
                         "rect": [150, 350, 330, 380], "actions": ["set_value", "focus"],
                         "class": "cos-link result-title"}]
    clicks = []

    async def click(args):
        clicks.append(args)
        return json.dumps({"status": "ok"})

    monkeypatch.setattr(domain_tools, "computer_mouse_click", click)
    result = await domain_tools._click_ref(context, "e1")
    assert result["status"] == "ok"
    assert clicks == [{"x": 290, "y": 465, "button": "left"}]


def test_press_accepts_combined_shortcut_shape():
    from reasonix_computer_use.domain_tools import _normalize_action, _press_parts

    assert _press_parts(["CTRL+L"]) == ("L", ["CTRL"])
    assert _press_parts(["ALT", "D"]) == ("D", ["ALT"])
    assert _normalize_action({"type": "press", "key": "Enter"})["keys"] == ["Enter"]
    assert _normalize_action({"type": "press", "key": "Control+a"})["keys"] == ["Control+a"]


def test_press_rejects_misspelled_modifier():
    from reasonix_computer_use.domain_tools import _validate_shortcut

    assert "CRTL" in _validate_shortcut("O", ["CRTL"])


@pytest.mark.asyncio
async def test_keyboard_supports_punctuation_shortcut(monkeypatch):
    from reasonix_computer_use import keyboard

    sent = []
    monkeypatch.setattr(keyboard, "_send_key",
                        lambda vk_code, key_up=False: sent.append((vk_code, key_up)))
    result = json.loads(await keyboard.computer_keyboard_press({"key": "+", "modifiers": ["ctrl"]}))
    assert result["status"] == "ok"
    assert "ctrl" in [value.casefold() for value in result["modifiers"]]
    assert "shift" in [value.casefold() for value in result["modifiers"]]
    assert sent


@pytest.mark.asyncio
async def test_pointer_multi_click_uses_requested_count(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "A", "A", (100, 200, 600, 700))
    calls = []

    async def click(args):
        calls.append(args)
        return json.dumps({"status": "ok"})

    monkeypatch.setattr(domain_tools, "computer_mouse_click", click)
    result = await domain_tools._execute(context, {
        "type": "multi_click", "x": 20, "y": 30, "count": 4, "button": "right"
    })
    assert result["status"] == "ok"
    assert calls[0]["x"] == 120 and calls[0]["y"] == 230
    assert calls[0]["click_count"] == 4
    assert calls[0]["button"] == "right"


@pytest.mark.asyncio
async def test_mouse_drag_releases_button_after_move_failure(monkeypatch):
    from reasonix_computer_use import mouse

    positions = iter([True, False])
    events = []
    monkeypatch.setattr(mouse.ctypes.windll.user32, "SetCursorPos", lambda *_a: next(positions))
    monkeypatch.setattr(mouse.ctypes.windll.user32, "mouse_event",
                        lambda flag, *_a: events.append(flag))
    with pytest.raises(OSError):
        await mouse.mouse_drag(0, 0, 100, 100, duration=0.05, steps=2)
    assert events[0] == mouse.MOUSEEVENTF_LEFTDOWN
    assert events[-1] == mouse.MOUSEEVENTF_LEFTUP


@pytest.mark.asyncio
async def test_horizontal_scroll_uses_horizontal_wheel(monkeypatch):
    from reasonix_computer_use import mouse

    events = []
    monkeypatch.setattr(mouse.ctypes.windll.user32, "mouse_event",
                        lambda *args: events.append(args))
    result = json.loads(await mouse.computer_mouse_scroll({"direction": "right", "lines": 2}))
    assert result["status"] == "ok"
    assert events[0][0] == mouse.MOUSEEVENTF_HWHEEL
    assert events[0][3] == 240


@pytest.mark.asyncio
async def test_press_repeat_is_bounded_and_repeated(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    calls = []
    monkeypatch.setattr(domain_tools, "_activate_for_keyboard", lambda _context: True)

    async def no_sleep(_seconds):
        return None

    async def press(args):
        calls.append(args)
        return json.dumps({"status": "ok"})

    monkeypatch.setattr(domain_tools.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(domain_tools, "computer_keyboard_press", press)
    result = await domain_tools._execute(WindowContext("w1", 1), {
        "type": "press", "keys": ["DOWN"], "repeat": 3, "interval": 0.02
    })
    assert result["repeat"] == 3
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_hold_key_releases_after_wait_failure(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    events = []
    monkeypatch.setattr(domain_tools, "_activate_for_keyboard", lambda _context: True)
    monkeypatch.setattr(domain_tools, "_send_key",
                        lambda code, key_up=False: events.append((code, key_up)))

    async def fail_sleep(_seconds):
        raise RuntimeError("interrupted")

    monkeypatch.setattr(domain_tools.asyncio, "sleep", fail_sleep)
    with pytest.raises(RuntimeError):
        await domain_tools._execute(WindowContext("w1", 1), {
            "type": "hold_key", "key": "space", "duration": 0.5
        })
    assert events == [(domain_tools.VK_MAP["space"], False),
                      (domain_tools.VK_MAP["space"], True)]


@pytest.mark.asyncio
async def test_unbalanced_key_down_is_rejected(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "A"}, "uia")
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _window_id: context)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "key_down", "key": "ctrl"}],
    }))
    assert result["code"] == "unbalanced_key_sequence"


@pytest.mark.asyncio
async def test_paired_key_is_released_when_later_action_fails(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "A"}, "uia")
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _window_id: context)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [])
    monkeypatch.setattr(domain_tools, "_capture_window", lambda *_a, **_k: (b"frame", {}))
    monkeypatch.setattr(domain_tools, "_wait_stable", lambda *_a, **_k: asyncio.sleep(0))
    released = []
    monkeypatch.setattr(domain_tools, "_send_key",
                        lambda code, key_up=False: released.append((code, key_up)))

    async def execute(_context, action):
        if action["type"] == "wait":
            return {"status": "error", "code": "synthetic_failure"}
        return {"status": "ok"}

    monkeypatch.setattr(domain_tools, "_execute", execute)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [
            {"type": "key_down", "key": "ctrl"},
            {"type": "wait", "seconds": 0},
            {"type": "key_up", "key": "ctrl"},
        ],
    }))
    assert result["code"] == "batch_stopped"
    assert released == [(domain_tools.VK_MAP["ctrl"], True)]


@pytest.mark.asyncio
async def test_select_cell_uses_spreadsheet_go_to(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    calls = []

    async def press(args):
        calls.append(("press", args))
        return json.dumps({"status": "ok"})

    async def type_text(args):
        calls.append(("type", args))
        return json.dumps({"status": "ok"})

    async def no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(domain_tools, "_activate_for_keyboard", lambda _context: True)
    monkeypatch.setattr(domain_tools, "computer_keyboard_press", press)
    monkeypatch.setattr(domain_tools, "computer_keyboard_type", type_text)
    monkeypatch.setattr(domain_tools.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(domain_tools, "_office_selection_address", lambda: "")
    observations = iter([{"elements": []}, {"elements": [
        {"ref": "e1", "role": "DataItem", "name": "A101", "selected": True}
    ]}])
    monkeypatch.setattr(domain_tools, "observe", lambda *_a, **_k: next(observations))
    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "Sheet", "XLMAIN", (0, 0, 1200, 800))
    result = await domain_tools._execute(context,
                                         {"type": "select_cell", "cell": "a101"})
    assert result["status"] == "ok"
    assert result["cell"] == "A101"
    assert calls[0] == ("press", {"key": "g", "modifiers": ["ctrl"]})
    assert calls[1][0] == "type"
    assert calls[1][1]["text"] == "A101"
    assert calls[2] == ("press", {"key": "enter", "modifiers": []})


@pytest.mark.asyncio
async def test_select_range_uses_spreadsheet_go_to(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    calls = []

    async def press(args):
        calls.append(("press", args))
        return json.dumps({"status": "ok"})

    async def type_text(args):
        calls.append(("type", args))
        return json.dumps({"status": "ok"})

    async def no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(domain_tools, "_activate_for_keyboard", lambda _context: True)
    monkeypatch.setattr(domain_tools, "computer_keyboard_press", press)
    monkeypatch.setattr(domain_tools, "computer_keyboard_type", type_text)
    monkeypatch.setattr(domain_tools.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(domain_tools, "_office_selection_address", lambda: "")
    observations = iter([{"elements": []}, {"elements": [
        {"ref": "e1", "role": "ComboBox", "name": "名称框", "value": "A1:A101"}
    ]}])
    monkeypatch.setattr(domain_tools, "observe", lambda *_a, **_k: next(observations))
    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "Sheet", "XLMAIN", (0, 0, 1200, 800))
    result = await domain_tools._execute(context,
                                         {"type": "select_range", "range": "a1:a101"})
    assert result["status"] == "ok"
    assert result["range"] == "A1:A101"
    assert result["selected"] is True
    assert calls[1][1]["text"] == "A1:A101"


@pytest.mark.asyncio
async def test_select_cell_rejects_unverified_selection(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    async def ok(_args):
        return json.dumps({"status": "ok"})

    async def no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(domain_tools, "_activate_for_keyboard", lambda _context: True)
    monkeypatch.setattr(domain_tools, "computer_keyboard_press", ok)
    monkeypatch.setattr(domain_tools, "computer_keyboard_type", ok)
    monkeypatch.setattr(domain_tools.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(domain_tools, "_office_selection_address", lambda: "")
    monkeypatch.setattr(domain_tools, "observe", lambda *_a, **_k: {"elements": []})
    result = await domain_tools._execute(WindowContext("w1", 1),
                                         {"type": "select_cell", "cell": "A1"})
    assert result["code"] == "selection_not_verified"


@pytest.mark.asyncio
async def test_save_as_requires_real_file_receipt(monkeypatch, tmp_path):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    destination = tmp_path / "saved.xlsx"
    context = WindowContext("w1", 1, app_name="Excel", owner_pid=10)
    context.info = lambda: WindowInfo(1, "工作簿1 - Excel", "XLMAIN", (0, 0, 800, 600), 10,
                                      r"C:\Office\EXCEL.EXE")
    monkeypatch.setattr(domain_tools, "_activate_for_keyboard", lambda _context: True)
    monkeypatch.setattr(domain_tools, "_active_office_application", lambda: None)
    monkeypatch.setattr(domain_tools.user32, "GetForegroundWindow", lambda: 1)
    monkeypatch.setattr(domain_tools, "get_window_info", lambda _hwnd: context.info())
    monkeypatch.setattr(domain_tools, "observe", lambda *_a, **_k: {"elements": [
        {"ref": "e1", "role": "ComboBox", "name": "文件名", "focused": True}
    ]})

    async def press(args):
        if args["key"] == "enter":
            destination.write_bytes(b"saved")
        return json.dumps({"status": "ok"})

    async def act(_args):
        return json.dumps({"status": "ok", "verified": True})

    async def no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(domain_tools, "computer_keyboard_press", press)
    monkeypatch.setattr(domain_tools, "uia_act", act)
    monkeypatch.setattr(domain_tools.asyncio, "sleep", no_sleep)
    result = await domain_tools._save_as(context, {"type": "save_as", "path": str(destination)})
    assert result["status"] == "ok"
    assert result["verified"] is True


@pytest.mark.asyncio
async def test_wps_f5_is_rejected(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "数字表格.xlsx - WPS Office", "OpusApp", (0, 0, 800, 600))
    result = await domain_tools._execute(context, {"type": "press", "keys": ["F5"]})
    assert result["code"] == "spreadsheet_f5_blocked"


def test_raw_pointer_action_requires_visible_change():
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "A", "Test", (0, 0, 100, 100))
    result = domain_tools._verify(context, {}, False, requires_change=True)
    assert result["verified"] is False


@pytest.mark.asyncio
async def test_visual_coordinate_click_rejects_animation_only_pixel_change(monkeypatch):
    from PIL import Image
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.update({"title": "QQ Music"}, "visual")
    context.info = lambda: WindowInfo(1, "QQ Music", "QQMusic", (50, 100, 550, 500))
    frames = iter([
        Image.new("RGB", (500, 400), "white"),
        Image.new("RGB", (500, 400), "black"),
    ])
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [])
    monkeypatch.setattr(domain_tools, "_capture_window", lambda *_a, **_k: (next(frames), context.info()))
    monkeypatch.setattr(domain_tools, "_wait_stable", lambda *_a, **_k: asyncio.sleep(0))
    monkeypatch.setattr(domain_tools, "_refresh_semantic", lambda *_a: False)
    monkeypatch.setattr(domain_tools, "_focused_input_state", lambda *_a, **_k: {
        "verified": False, "method": "uia-win32-focus",
        "reason": "no_focused_editable_control",
    })

    executed = []

    async def execute(_context, _action):
        executed.append(True)
        return {"status": "ok", "method": "mouse_click"}

    monkeypatch.setattr(domain_tools, "_execute", execute)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "click_point", "x": 280, "y": 85}],
    }))

    assert result["code"] == "completion_evidence_required"
    assert executed == []
    assert result["required_evidence"] == {"visual_click": ["expect"]}


@pytest.mark.asyncio
async def test_visual_semantic_click_cannot_be_hidden_by_later_action(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "Canvas"}, "visual")
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "_execute",
                        lambda *_a, **_k: pytest.fail("unsafe batch must be rejected before execution"))
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [
            {"type": "click_point", "x": 100, "y": 100},
            {"type": "press", "keys": ["ENTER"]},
        ],
        "expect": {"text_present": "完成"},
    }))
    assert result["code"] == "visual_action_must_be_terminal"


@pytest.mark.asyncio
async def test_visual_coordinate_revision_is_checked_without_mcp_restart(monkeypatch, tmp_path):
    from PIL import Image
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "Canvas", "Canvas", (10, 20, 210, 220))
    context = WindowContext("w1", 1)
    context.update({"title": "Canvas"}, "visual")
    context.info = lambda: info
    reference = Image.new("RGB", (200, 200), "white")
    reference.save(tmp_path / f"state_{context.window_id}_{context.revision}.png")
    context.image_hash = domain_tools._image_digest(reference)
    context.visual_rect = info.rect
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "_get_screenshot_dir", lambda: str(tmp_path))
    monkeypatch.setattr(domain_tools, "_capture_window",
                        lambda *_a, **_k: (Image.new("RGB", (200, 200), "black"), info))
    monkeypatch.setattr(domain_tools, "_execute",
                        lambda *_a, **_k: pytest.fail("stale visual coordinate must not execute"))

    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "click_point", "x": 100, "y": 100}],
        "expect": {"text_present": "完成"},
    }))
    assert result["code"] == "stale_revision"


@pytest.mark.asyncio
async def test_visual_playback_click_is_verified_only_by_semantic_contract(monkeypatch, tmp_path):
    from PIL import Image
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.update({"title": "Music"}, "visual")
    context.goal = "播放枫"
    context.completion_contract = {"playback": "*"}
    info = WindowInfo(1, "Music", "Music", (0, 0, 500, 400))
    context.info = lambda: info
    reference = Image.new("RGB", (500, 400), "white")
    context.image_hash = domain_tools._image_digest(reference)
    context.visual_rect = info.rect
    reference.save(tmp_path / f"state_{context.window_id}_{context.revision}.png")
    frames = iter([Image.new("RGB", (500, 400), "white"),
                   Image.new("RGB", (500, 400), "white"),
                   Image.new("RGB", (500, 400), "black")])
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [])
    monkeypatch.setattr(domain_tools, "_capture_window", lambda *_a, **_k: (next(frames), info))
    monkeypatch.setattr(domain_tools, "_get_screenshot_dir", lambda: str(tmp_path))
    monkeypatch.setattr(domain_tools, "_wait_stable", lambda *_a, **_k: asyncio.sleep(0))
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "remember_success", lambda *_a, **_k: None)
    monkeypatch.setattr(domain_tools, "_focused_input_state", lambda *_a, **_k: {
        "verified": True, "method": "retained-input-focus", "focus_hwnd": "0x2",
    })

    async def execute(_context, _action):
        return {"status": "ok", "method": "mouse_click"}

    def refresh(_context):
        context.elements = [{"role": "Button", "name": "暂停"},
                            {"role": "Text", "name": "枫", "selected": True}]
        return True

    monkeypatch.setattr(domain_tools, "_execute", execute)
    monkeypatch.setattr(domain_tools, "_refresh_semantic", refresh)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "click_point", "x": 100, "y": 100}],
        "expect": {"playback_active": True, "playback_target": "枫"},
    }))
    assert result["status"] == "ok"
    assert result["verification"]["method"] == "semantic-contract"
    assert all(check["verified"] for check in result["verification"]["checks"])
    assert result["results"][0]["verified"] is True
    assert result["results"][0]["focus_effect"]["verified"] is False
    assert context.completed_intents == {"playback"}
    assert context.completion_evidence["playback"]["method"] == "semantic-contract"


def test_preexisting_target_title_remains_pending_until_playback_action_verified():
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(
        1, "枫 (2007世界巡回演唱会) - 周杰伦", "Music", (0, 0, 500, 400))
    assert domain_tools._record_task_goal(
        context, "确认当前状态", "打开QQ音乐并播放周杰伦的枫") is None
    # App launch can be complete, but a title that existed before any media
    # action cannot satisfy playback for this task.
    domain_tools._mark_completion(context, "open", "window_activate", "应用已位于前台")
    status = domain_tools._task_completion(context)
    assert status["status"] == "pending"
    assert status["verified"] is False
    assert status["completed"] == ["open"]
    assert status["pending"] == ["playback"]


def test_unspecified_media_variant_does_not_satisfy_exact_target_transition():
    from reasonix_computer_use import domain_tools

    before = {"title": "其他歌曲", "target_selected": False,
              "target_active_label": False, "target_near_active": False}
    live = {"title": "枫 (Live) - 周杰伦", "target_selected": False,
            "target_active_label": False, "target_near_active": False}
    concert = {"title": "枫 (2007世界巡回演唱会) - 周杰伦", "target_selected": False,
               "target_active_label": False, "target_near_active": False}
    original = {"title": "枫 - 周杰伦", "target_selected": False,
                "target_active_label": False, "target_near_active": False}

    assert domain_tools._target_transition(before, live, "枫") == (
        False, "target_only_in_existing_result")
    assert domain_tools._target_transition(before, concert, "枫") == (
        False, "target_only_in_existing_result")
    assert domain_tools._target_transition(before, original, "枫") == (
        True, "new_target_in_window_title")
    assert domain_tools._playback_target_matches_goal("播放枫", "枫 Live") is False
    assert domain_tools._playback_target_matches_goal("播放枫 Live", "枫 Live") is True


def test_playback_snapshot_does_not_select_related_variant(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "Music", "Music", (0, 0, 500, 400))
    monkeypatch.setattr(domain_tools, "find_text", lambda *_a, **_k: {"matches": []})
    snapshot = domain_tools._playback_snapshot(context, "枫", [
        {"role": "Text", "name": "枫 (Live) - 周杰伦", "selected": True},
        {"role": "Text", "name": "枫 - 周杰伦", "selected": False},
    ])
    assert snapshot["target_selected"] is False
    original = domain_tools._playback_snapshot(context, "枫", [
        {"role": "Text", "name": "枫 - 周杰伦", "selected": True},
    ])
    assert original["target_selected"] is True


def test_navigation_shortcuts_require_change_but_copy_does_not():
    from reasonix_computer_use.domain_tools import _requires_observable_change

    assert _requires_observable_change({"type": "press", "keys": ["CTRL", "O"]}) is True
    assert _requires_observable_change({"type": "press", "keys": ["CTRL", "C"]}) is False
    assert _requires_observable_change({"type": "click_ref", "ref": "e1"}) is True
    assert _requires_observable_change({"type": "submit"}) is True


def test_retained_input_focus_never_proves_a_visual_pointer_effect():
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.goal = "搜索音乐"
    retained = {
        "verified": True, "method": "retained-input-focus", "focus_hwnd": "0x1",
    }
    result = domain_tools._pointer_focus_effect(
        context, {"type": "click_point", "x": 20, "y": 20}, retained, 1,
        followed_by_input=True)
    assert result["verified"] is False
    assert result["reason"] == "retained_focus_does_not_prove_pointer_effect"


def test_typed_text_ocr_must_be_inside_target_input(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "Search", "WebView", (0, 0, 400, 300))
    context.elements = [{"ref": "e1", "role": "Edit", "name": "Search",
                         "rect": [10, 10, 190, 45]}]
    context.references = {"e1": context.elements[0]}
    monkeypatch.setattr(domain_tools, "observe", lambda *_a, **_k: {"elements": []})
    monkeypatch.setattr(domain_tools, "find_text", lambda *_a, **_k: {"matches": [
        {"text": "deepseek", "rect": [220, 180, 300, 205], "confidence": 0.99},
    ]})
    result = domain_tools._verify_typed_text(
        context, {"type": "type", "text": "deepseek", "_target_ref": "e1"},
        {"method": "send_input"})
    assert result["verified"] is False
    assert result["method"] == "ocr-target-region"


def test_typed_text_ocr_accepts_match_inside_target_input(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "Search", "WebView", (0, 0, 400, 300))
    context.elements = [{"ref": "e1", "role": "Edit", "name": "Search",
                         "rect": [10, 10, 190, 45]}]
    context.references = {"e1": context.elements[0]}
    monkeypatch.setattr(domain_tools, "observe", lambda *_a, **_k: {"elements": []})
    monkeypatch.setattr(domain_tools, "find_text", lambda *_a, **_k: {"matches": [
        {"text": "deepseek", "rect": [20, 15, 100, 38], "confidence": 0.99},
    ]})
    result = domain_tools._verify_typed_text(
        context, {"type": "type", "text": "deepseek", "_target_ref": "e1"},
        {"method": "send_input"})
    assert result["verified"] is True
    assert result["method"] == "ocr-target-region"


def test_playback_requires_new_state_and_all_expectations(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.elements = [{"role": "Button", "name": "暂停"}]
    context.info = lambda: WindowInfo(1, "Music", "Music", (0, 0, 400, 300))
    unchanged = domain_tools._verify(
        context, {"playback_active": True}, changed=False, requires_change=True)
    assert unchanged["verified"] is False

    monkeypatch.setattr(domain_tools, "find_text", lambda *_a, **_k: {"matches": []})
    mismatch = domain_tools._verify(
        context, {"playback_active": True, "text_present": "枫"},
        changed=True, requires_change=True)
    assert mismatch["verified"] is False
    assert mismatch["method"] == "semantic-contract"
    assert len(mismatch["checks"]) == 2


def test_existing_file_cannot_be_used_as_proof_of_this_save(tmp_path):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    target = tmp_path / "book.xlsx"
    target.write_bytes(b"same")
    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "WPS", "OpusApp", (0, 0, 400, 300))
    baseline = domain_tools._verification_baseline(context, {"file_exists": str(target)})
    result = domain_tools._verify(
        context, {"file_exists": str(target)}, changed=False,
        requires_change=False, baseline=baseline)
    assert result["verified"] is False
    assert result["method"] == "filesystem-exists-transition"
    target.write_bytes(b"changed")
    changed = domain_tools._verify(
        context, {"file_exists": str(target)}, changed=False,
        requires_change=False, baseline=baseline)
    assert changed["verified"] is True


def test_static_value_and_paths_cannot_prove_a_new_action(tmp_path, monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "Synthetic", "Synthetic", (0, 0, 400, 300))
    context.elements = [{"role": "Text", "name": "210"}]
    monkeypatch.setattr(domain_tools, "find_text", lambda *_a, **_k: {"matches": []})

    expect = {
        "value_equals": "210",
        "source_absent": str(source),
        "destination_present": str(destination),
    }
    baseline = domain_tools._verification_baseline(context, expect)
    unchanged = domain_tools._verify(
        context, expect, changed=True, requires_change=True, baseline=baseline)
    assert unchanged["verified"] is False

    source.unlink()
    destination.write_text("new content", encoding="utf-8")
    context.elements = [{"role": "Text", "name": "211"}]
    transitioned = domain_tools._verify(
        context, {"source_absent": str(source), "destination_present": str(destination)},
        changed=True, requires_change=True, baseline=baseline)
    assert transitioned["verified"] is True


def test_ocr_text_present_must_be_newly_observed(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "Synthetic", "Synthetic", (0, 0, 400, 300))
    context.elements = []
    monkeypatch.setattr(domain_tools, "find_text", lambda *_a, **_k: {
        "matches": [{"text": "完成", "rect": [10, 10, 40, 30]}],
    })
    baseline = domain_tools._verification_baseline(context, {"text_present": "完成"})
    result = domain_tools._verify(
        context, {"text_present": "完成"}, changed=True,
        requires_change=True, baseline=baseline)
    assert result["verified"] is False


def test_terminal_intents_require_domain_specific_expectations():
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.elements = [
        {"ref": "e1", "role": "Button", "name": "播放"},
        {"ref": "e2", "role": "Button", "name": "保存"},
    ]
    missing_play = domain_tools._missing_completion_evidence(
        context, [{"type": "click_ref", "ref": "e1"}], {})
    assert missing_play == {"playback": ["playback_active", "playback_target"]}
    missing_save = domain_tools._missing_completion_evidence(
        context, [{"type": "click_ref", "ref": "e2"}], {})
    assert missing_save == {"save": ["file_exists"]}
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "click_ref", "ref": "e1"}],
        {"playback_active": True}) == {"playback": ["playback_target"]}


def test_playback_goal_applies_to_result_click_but_not_search_submission():
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.goal = "找到周杰伦的枫并播放"
    context.elements = [{"ref": "e1", "role": "Edit", "name": "搜索音乐"}]
    search = [
        {"type": "click_ref", "ref": "e1"},
        {"type": "type", "text": "周杰伦 枫"},
        {"type": "submit"},
    ]
    assert domain_tools._missing_completion_evidence(context, search, {}) == {}
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "click_text", "text": "枫"}], {}) == {
            "playback": ["playback_active", "playback_target"]
        }
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "click_text", "text": "枫"}],
        {"playback_active": True, "playback_target": "晴天"}) == {
            "playback": ["playback_target:枫"]
        }
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "click_text", "text": "枫"}],
        {"playback_active": True, "playback_target": "枫"}) == {}
    assert domain_tools._playback_target_hint("播放《Shape of You》") == "shape of you"
    assert domain_tools._playback_target_hint("播放周杰伦的歌") == "周杰伦"
    assert domain_tools._playback_target_hint("播放第一首歌") == ""
    assert domain_tools._playback_target_hint("点击播放按钮") == ""
    context.elements = [{"ref": "o1", "role": "text", "name": "搜索音乐"}]
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "click_ref", "ref": "o1"}], {}) == {}
    context.elements = [{"ref": "e1", "role": "Edit", "name": "搜索音乐"}]
    context.focused_ref = "e1"
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "press", "keys": ["Enter"]}], {}) == {}
    context.focused_ref = ""
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "press", "keys": ["Enter"]}], {}) == {
            "playback": ["playback_active", "playback_target"]
        }
    context.goal = "请打开文档并倾听提示音"
    assert domain_tools._completion_requirements(
        context, [{"type": "click_text", "text": "提示"}]) == {}
    context.goal = "放我喜欢的歌听"
    assert domain_tools._completion_requirements(
        context, [{"type": "click_text", "text": "周杰伦"}]) == {}
    assert domain_tools._completion_requirements(
        context, [{"type": "click_text", "text": "周杰伦",
                   "purpose": "playback target"}]) == {
            "playback": {"playback_active", "playback_target"}}


def test_observation_subgoals_cannot_erase_terminal_completion_contract():
    """Perception queries must not turn a playback result click into success."""
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w-contract", 1)
    assert domain_tools._record_task_goal(
        context, "定位搜索框", "打开 QQ 音乐并播放周杰伦的《枫》") is None
    # These are deliberately narrow follow-up queries like the ones emitted
    # by an agent while waiting for search results.
    assert domain_tools._record_task_goal(context, "确认搜索结果是否稳定") is None
    assert domain_tools._record_task_goal(context, "确认当前界面") is None
    assert context.observation_goal == "确认当前界面"
    assert context.task_goal == "打开 QQ 音乐并播放周杰伦的《枫》"
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "click_text", "text": "枫"}], {}) == {
            "playback": ["playback_active", "playback_target"]}
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "click_text", "text": "枫"}],
        {"playback_active": True, "playback_target": "晴天"}) == {
            "playback": ["playback_target:枫"]}
    # "打开 QQ 音乐" was already verified by computer_app. A later
    # double-click on the song must prove playback, not re-prove app launch.
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "double_click", "text": "枫"}],
        {"playback_active": True, "playback_target": "枫"}) == {}


def test_task_contract_keeps_open_save_navigation_intents_after_subgoal():
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w-contract-2", 1)
    assert domain_tools._record_task_goal(
        context, "定位文件", "打开报告并保存，然后访问 https://example.com") is None
    assert domain_tools._record_task_goal(context, "检查文件名") is None
    # The action-level intent detector still decides whether a step is
    # terminal, but it reads the monotonic contract rather than the latest
    # observation wording.
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "double_click", "purpose": "文件"}], {}) == {
            "open": ["window_title_contains", "text_present", "location_contains"]}
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "press", "keys": ["CTRL", "S"]}], {}) == {
            "save": ["file_exists"]}
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "press", "keys": ["CTRL", "L"]},
                   {"type": "type", "text": "https://example.com"},
                   {"type": "submit"}], {}) == {
            "navigation": ["window_title_contains", "text_present", "location_contains"]}


def test_task_goal_conflict_is_rejected_without_erasing_existing_contract():
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w-contract-3", 1)
    assert domain_tools._record_task_goal(context, "搜索框", "播放《枫》") is None
    result = domain_tools._record_task_goal(context, "搜索框", "播放《晴天》")
    assert result["code"] == "task_goal_conflict"
    assert context.hard_blocked is True
    assert context.task_goal == "播放《枫》"
    assert domain_tools._contract_target_matches(context, "枫") is True
    assert domain_tools._contract_target_matches(context, "晴天") is False


def test_playback_contract_does_not_treat_result_tab_as_terminal_action():
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w-contract-tabs", 1)
    assert domain_tools._record_task_goal(
        context, "定位搜索结果", "打开QQ音乐并播放周杰伦的《枫》") is None
    context.references = {
        "tab": {"ref": "tab", "role": "TabItem", "name": "单曲"},
        "song": {"ref": "song", "role": "ListItem", "name": "枫 - 周杰伦"},
    }
    context.elements = list(context.references.values())

    assert domain_tools._missing_completion_evidence(
        context, [{"type": "click_ref", "ref": "tab"}], {}) == {}
    assert domain_tools._missing_completion_evidence(
        context, [{"type": "double_click", "ref": "song"}], {}) == {
            "playback": ["playback_active", "playback_target"]}


def test_restored_contract_requires_rebinding_original_task_goal():
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    original = "播放《枫》"
    context = WindowContext("w-contract-4", 1)
    context.task_goal_hash = hashlib.sha256(
        domain_tools._compact_goal_text(original).encode("utf-8")).hexdigest()[:32]
    context.task_goal_explicit = True
    context.completion_contract = {"playback": f"{'b' * 32}:1"}

    missing = domain_tools._record_task_goal(context, "确认当前界面")
    assert missing["code"] == "task_goal_required_after_restart"
    assert domain_tools._record_task_goal(context, "确认当前界面", original) is None
    assert context.task_goal == original


def test_uia_generic_play_control_is_not_sufficient_for_named_goal():
    from reasonix_computer_use import domain_tools

    assert domain_tools._uia_goal_sufficient(
        [{"role": "Button", "name": "播放"}], "播放周杰伦的枫") is False
    assert domain_tools._uia_goal_sufficient(
        [{"role": "Edit", "name": "搜索音乐"}], "搜索周杰伦的枫") is True
    assert domain_tools._uia_goal_sufficient(
        [{"role": "Text", "name": "枫"}], "播放周杰伦的枫") is True


@pytest.mark.asyncio
async def test_auto_state_skips_generic_uia_play_control_for_named_target(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "Music", "Music", (0, 0, 400, 300))
    context = WindowContext("w1", 1)
    context.update({"title": "Music"}, "window")
    context.info = lambda: info
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _window_id: context)
    monkeypatch.setattr(domain_tools, "observe", lambda *_a, **_k: {"elements": [{
        "ref": "e1", "role": "Button", "name": "播放", "rect": [10, 10, 60, 35],
        "coordinate_space": "window",
    }]})
    monkeypatch.setattr(domain_tools, "_ocr_elements", lambda *_a, **_k: ([{
        "ref": "o1", "role": "text", "name": "枫", "rect": [20, 80, 60, 100],
        "confidence": 0.99, "coordinate_space": "window",
    }], {"engine": "rapid", "stable": True, "relevant": 1}))

    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "播放周杰伦的枫",
        "task_goal": "播放周杰伦的枫", "mode": "auto",
    }))
    assert result["source"] == "ocr"
    assert result["sufficient"] is True
    assert [item["name"] for item in result["elements"]] == ["枫"]


@pytest.mark.asyncio
async def test_generic_play_memory_cannot_override_named_result_ocr(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "Music", "Music", (0, 0, 400, 300))
    context = WindowContext("w1", 1)
    context.update({"title": "Music"}, "window")
    context.info = lambda: info
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _window_id: context)
    monkeypatch.setattr(domain_tools, "memory_candidates", lambda *_a, **_k: [{
        "action": {"type": "click_text", "text": "播放"}, "successes": 9,
    }])
    monkeypatch.setattr(domain_tools, "observe", lambda *_a, **_k: {"elements": [{
        "ref": "e1", "role": "TabItem", "name": "播放", "rect": [10, 10, 60, 35],
        "coordinate_space": "window",
    }]})
    seen_goals = []

    def ocr(_window_id, goal, _limit, _engine):
        seen_goals.append(goal)
        return ([{"ref": "o1", "role": "text", "name": "枫VIP臻品母带",
                  "rect": [100, 100, 220, 130], "confidence": 0.95,
                  "coordinate_space": "window"}],
                {"engine": "rapid", "stable": True, "relevant": 1})

    monkeypatch.setattr(domain_tools, "_ocr_elements", ocr)
    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "查看搜索结果中的具体目标",
        "task_goal": "打开音乐应用并播放周杰伦的枫", "mode": "auto",
    }))
    assert result["source"] == "ocr"
    assert result["elements"][0]["name"] == "枫VIP臻品母带"
    assert any("播放 枫" in goal for goal in seen_goals)
    assert result["recommended_action"] == {"type": "double_click", "ref": "o1"}
    assert result["recommended_expect"] == {
        "playback_active": True, "playback_target": "枫"}


@pytest.mark.asyncio
async def test_input_ready_observation_does_not_consume_visual_budget(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.update({"title": "Music"}, "uia", [{
        "ref": "e1", "role": "ComboBox", "name": "搜索音乐", "focused": True,
    }])
    context.input_focus_verified = True
    context.info = lambda: WindowInfo(1, "Music", "Music", (0, 0, 400, 300))
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _window_id: context)
    monkeypatch.setattr(domain_tools, "_focused_input_state", lambda *_a, **_k: {
        "verified": True, "method": "uia-focused-input", "ref": "e1", "role": "ComboBox",
    })
    monkeypatch.setattr(domain_tools, "_capture_window",
                        lambda *_a, **_k: pytest.fail("input-ready state must not capture vision"))
    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "搜索框已聚焦，需要输入搜索词",
        "task_goal": "打开音乐应用并播放周杰伦的枫",
    }))
    assert result["source"] == "uia"
    assert result["input_ready"] is True
    assert result["recommended_action"]["type"] == "type"
    assert context.visual_count == 0


def test_playback_target_rejects_existing_search_result_as_identity(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "Music", "Music", (0, 0, 400, 300))
    context.elements = [{"role": "Button", "name": "暂停"},
                        {"role": "Text", "name": "枫", "rect": [20, 50, 80, 70]}]
    monkeypatch.setattr(domain_tools, "find_text", lambda *_a, **_k: {"matches": []})
    baseline = domain_tools._verification_baseline(
        context, {"playback_active": True, "playback_target": "枫"})
    context.elements.append({"role": "Text", "name": "无关界面变化"})
    result = domain_tools._verify(
        context, {"playback_active": True, "playback_target": "枫"},
        changed=False, requires_change=True, baseline=baseline)
    assert result["verified"] is False
    assert result["method"] == "semantic-contract"
    target_check = next(item for item in result["checks"] if item["key"] == "playback_target")
    assert target_check["evidence"] == "target_only_in_existing_result"


def test_playback_target_accepts_new_selected_current_track(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.info = lambda: WindowInfo(1, "Music", "Music", (0, 0, 400, 300))
    context.elements = [{"role": "Button", "name": "暂停"},
                        {"role": "Text", "name": "枫", "selected": False}]
    monkeypatch.setattr(domain_tools, "find_text", lambda *_a, **_k: {"matches": []})
    baseline = domain_tools._verification_baseline(
        context, {"playback_active": True, "playback_target": "枫"})
    context.elements = [{"role": "Button", "name": "暂停"},
                        {"role": "Text", "name": "枫", "selected": True}]
    result = domain_tools._verify(
        context, {"playback_active": True, "playback_target": "枫"},
        changed=False, requires_change=True, baseline=baseline)
    assert result["verified"] is True
    assert result["method"] == "semantic-contract"


@pytest.mark.asyncio
async def test_submit_sends_enter_to_single_line_edit(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.elements = [{"ref": "e1", "role": "Edit", "name": "搜索歌曲", "id": "SongSearch"}]
    context.focused_ref = "e1"
    monkeypatch.setattr(domain_tools, "_activate_for_keyboard", lambda _context: True)
    calls = []

    async def press(args):
        calls.append(args)
        return json.dumps({"status": "ok"})

    monkeypatch.setattr(domain_tools, "computer_keyboard_press", press)
    result = await domain_tools._execute(context, {"type": "submit"})
    assert result["status"] == "ok"
    assert result["method"] == "keyboard_enter"
    assert calls == [{"key": "enter", "modifiers": []}]


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [
    {"ref": "e1", "role": "Document", "name": "正文"},
    {"ref": "e1", "role": "Edit", "name": "聊天消息", "id": "MultilineInput"},
])
async def test_submit_rejects_document_and_multiline_editor(monkeypatch, target):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.elements = [target]
    context.focused_ref = "e1"
    monkeypatch.setattr(domain_tools, "computer_keyboard_press",
                        lambda _args: pytest.fail("unsafe submit sent Enter"))
    result = await domain_tools._execute(context, {"type": "submit"})
    assert result["code"] == "submit_not_safe"


@pytest.mark.asyncio
async def test_type_then_submit_batch_keeps_input_ref(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.update({"title": "Music"}, "uia", [
        {"ref": "e1", "role": "Edit", "name": "搜索歌曲", "id": "SongSearch"}
    ])
    context.info = lambda: WindowInfo(1, "Music", "MusicWindow", (0, 0, 500, 500))
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _window_id: context)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [])
    monkeypatch.setattr(domain_tools, "reserve_text_input", lambda **_kwargs: True)
    monkeypatch.setattr(domain_tools, "_wait_stable", lambda *_a, **_k: asyncio.sleep(0))
    monkeypatch.setattr(domain_tools, "_refresh_semantic", lambda *_a: True)
    monkeypatch.setattr(domain_tools, "_verify_typed_text", lambda *_a, **_k: {"verified": True})
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "remember_success", lambda *_a: None)
    monkeypatch.setattr(domain_tools, "_capture_window", lambda *_a, **_k: (b"frame", {}))
    seen = []

    async def execute(_context, action):
        seen.append(dict(action))
        return {"status": "ok", "method": "test"}

    monkeypatch.setattr(domain_tools, "_execute", execute)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [
            {"type": "click_ref", "ref": "e1"},
            {"type": "type", "text": "周杰伦"},
            {"type": "submit"},
        ],
    }))
    assert result["status"] == "ok"
    assert seen[1]["_target_ref"] == "e1"
    assert seen[2]["_target_ref"] == "e1"


@pytest.mark.asyncio
async def test_verified_input_then_next_revision_enter_is_cross_call_submit(monkeypatch):
    """A playback task must submit its search before terminal playback evidence is required."""
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.update({"title": "Music", "value": ""}, "uia", [
        {"ref": "e1", "role": "ComboBox", "name": "搜索音乐", "automation_id": "SearchBox"}
    ])
    context.focused_ref = "e1"
    context.task_goal = "打开音乐应用并播放周杰伦的枫"
    context.goal = context.task_goal
    context.completion_contract = {"playback": "*"}
    context.info = lambda: WindowInfo(1, "Music", "MusicWindow", (0, 0, 500, 500))
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _window_id: context)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [])
    monkeypatch.setattr(domain_tools, "reserve_text_input", lambda **_kwargs: True)
    monkeypatch.setattr(domain_tools, "_wait_stable", lambda *_a, **_k: asyncio.sleep(0))
    monkeypatch.setattr(domain_tools, "_refresh_semantic", lambda *_a: True)
    monkeypatch.setattr(domain_tools, "_verify_typed_text", lambda *_a, **_k: {"verified": True})
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "remember_success", lambda *_a: None)
    monkeypatch.setattr(domain_tools, "_capture_window", lambda *_a, **_k: (b"frame", {}))
    focus_hwnd = 77
    monkeypatch.setattr(domain_tools, "_gui_focus_hwnd", lambda _context: focus_hwnd)
    seen = []

    async def execute(_context, action):
        seen.append(dict(action))
        return {"status": "ok", "method": "test"}

    monkeypatch.setattr(domain_tools, "_execute", execute)
    typed = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "type", "text": "周杰伦 枫", "purpose": "search"}],
    }))
    assert typed["status"] == "ok"
    assert typed["input_typed"] is True
    assert typed["input_submitted"] is False
    assert typed["recommended_action"] == {"type": "submit"}
    assert context.last_input_task_nonce == context.input_task_nonce

    # Typing changes the semantic revision and UIA may assign a new short ref.
    context.update({"title": "Music", "value": "周杰伦 枫"}, "uia", [
        {"ref": "e2", "role": "ComboBox", "name": "搜索音乐", "automation_id": "SearchBox",
         "focused": True}
    ])
    monkeypatch.setattr(domain_tools, "_focused_input_state", lambda *_a, **_k: {
        "verified": True, "method": "uia-focused-input", "ref": "e2", "role": "ComboBox",
        "focus_hwnd": hex(focus_hwnd),
    })
    submitted = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "press", "key": "Return"}],
    }))

    assert submitted["status"] == "ok"
    assert submitted["input_submitted"] is True
    assert submitted["recommended_action"]["tool"] == "computer_state"
    assert seen[-1]["type"] == "submit"
    assert seen[-1]["_target_ref"] == "e2"
    assert seen[-1]["_normalized_from"] == "press_enter"
    assert context.last_input_task_nonce == ""


def test_enter_after_input_focus_leaves_remains_terminal_playback_action(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.goal = "播放周杰伦的枫"
    context.completion_contract = {"playback": "*"}
    context.last_input_task_nonce = context.input_task_nonce
    context.last_input_selector = {"role": "Edit", "name": "搜索音乐"}
    monkeypatch.setattr(domain_tools, "_focused_input_state", lambda *_a, **_k: {
        "verified": False, "reason": "no_focused_editable_control"})
    actions = [{"type": "press", "keys": ["Enter"]}]
    domain_tools._normalize_recent_enter_submit(context, actions)
    assert actions[0]["type"] == "press"
    assert domain_tools._missing_completion_evidence(context, actions, {}) == {
        "playback": ["playback_active", "playback_target"]}


def test_first_state_requires_explicit_task_goal():
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    missing = domain_tools._record_task_goal(context, "定位搜索框")
    assert missing["code"] == "task_goal_required"
    assert context.task_goal == ""
    assert domain_tools._record_task_goal(
        context, "定位搜索框", "打开音乐应用并播放指定歌曲") is None


@pytest.mark.asyncio
async def test_empty_computer_action_returns_canonical_example():
    from reasonix_computer_use import domain_tools

    result = json.loads(await domain_tools.computer_action({}))
    assert result["code"] == "invalid_action_request"
    assert result["blocked"] is False
    assert result["canonical_example"]["actions"][0]["type"] == "type"


@pytest.mark.asyncio
async def test_ctrl_l_type_submit_allows_verified_navigation_context(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.update({"title": "Browser"}, "uia", [])
    title = ["Browser"]
    context.info = lambda: WindowInfo(1, title[0], "Chrome_WidgetWin_1", (0, 0, 500, 500))
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _window_id: context)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [])
    monkeypatch.setattr(domain_tools, "reserve_text_input", lambda **_kwargs: True)
    monkeypatch.setattr(domain_tools, "_wait_stable", lambda *_a, **_k: asyncio.sleep(0))
    monkeypatch.setattr(domain_tools, "_refresh_semantic", lambda *_a: True)
    monkeypatch.setattr(domain_tools, "_verify_typed_text", lambda *_a, **_k: {"verified": True})
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "remember_success", lambda *_a: None)
    monkeypatch.setattr(domain_tools, "_capture_window", lambda *_a, **_k: (b"frame", {}))
    seen = []

    async def execute(_context, action):
        seen.append(dict(action))
        if action.get("type") == "submit":
            title[0] = "Baidu"
        return {"status": "ok", "method": "test"}

    monkeypatch.setattr(domain_tools, "_execute", execute)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [
            {"type": "press", "keys": ["CTRL", "L"]},
            {"type": "type", "text": "https://www.baidu.com"},
            {"type": "submit"},
        ],
        "expect": {"window_title_contains": "Baidu"},
    }))
    assert result["status"] == "ok"
    assert seen[2]["_trusted_submit_context"] is True


def test_find_app_window_does_not_match_partial_browser_title(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.windows import WindowInfo

    edge = WindowInfo(1, "WPS - 搜索 - Microsoft Edge", "Chrome_WidgetWin_1",
                      (0, 0, 1000, 700), 10, r"C:\Edge\msedge.exe")
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [edge])
    assert domain_tools._find_app_window(
        {"name": "WPS", "path": r"C:\WPS\wps.exe"}, timeout=0.01) is None


def test_find_app_window_ignores_minimized_placeholder(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.windows import WindowInfo

    hidden = WindowInfo(1, "WPS", "OpusApp", (-32000, -32000, -31840, -31972),
                        10, r"C:\WPS\wps.exe")
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [hidden])
    assert domain_tools._find_app_window(
        {"name": "WPS", "path": r"C:\WPS\wps.exe"}, timeout=0.01) is None


def test_spreadsheet_hint_only_recommends_explicit_range():
    from reasonix_computer_use.domain_tools import _spreadsheet_hint
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "数字表格.xlsx - WPS Office", "OpusApp", (0, 0, 800, 600))
    generic = _spreadsheet_hint(info, "查看表格数据")
    explicit = _spreadsheet_hint(info, "选择 A1:A101")
    assert "recommended_action" not in generic
    assert explicit["recommended_action"] == {"type": "select_range", "range": "A1:A101"}


def test_excel_window_gets_spreadsheet_hint():
    from reasonix_computer_use.domain_tools import _spreadsheet_hint
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "工作簿1 - Excel", "XLMAIN", (0, 0, 800, 600))
    hint = _spreadsheet_hint(info, "在 A1:A100 填入数据")
    assert hint["recommended_action"] == {"type": "select_range", "range": "A1:A100"}


@pytest.mark.asyncio
async def test_targeted_type_selects_existing_text_before_sendinput(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.focused_ref = "e1"
    selected = []

    async def uia_act(_args):
        return json.dumps({"status": "error", "code": "pattern_unavailable"})

    async def select_all():
        selected.append(True)
        return {"status": "ok"}

    async def keyboard_type(args):
        return json.dumps({"status": "ok", "method": "send_input",
                           "text_length": len(args["text"])})

    monkeypatch.setattr(domain_tools, "uia_act", uia_act)
    monkeypatch.setattr(domain_tools, "_activate_for_keyboard", lambda _context: True)
    monkeypatch.setattr(domain_tools, "_select_all", select_all)
    monkeypatch.setattr(domain_tools, "computer_keyboard_type", keyboard_type)
    result = await domain_tools._execute(context, {"type": "type", "text": "deepseek"})
    assert result["status"] == "ok"
    assert selected == [True]


def test_ocr_rejects_occluded_target_window(monkeypatch):
    import pytest
    pytest.skip("v0.8.0-beta.2: OCR shim uses new perception layer — migrate to test_perception.py")
    from reasonix_computer_use import text_vision
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(11, "Edge", "Chrome_WidgetWin_1", (0, 0, 800, 600))
    monkeypatch.setattr(text_vision, "_capture_window", lambda *_a, **_k: (object(), info))
    monkeypatch.setattr(text_vision.user32, "GetForegroundWindow", lambda: 22)
    with pytest.raises(RuntimeError, match="前台焦点"):
        text_vision.scan_text("w1")


@pytest.mark.asyncio
async def test_failed_sendinput_uses_one_verified_clipboard_fallback(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.update({"title": "WebView"}, "uia")
    context.info = lambda: WindowInfo(1, "WebView", "Chrome_WidgetWin", (0, 0, 500, 500))
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [])
    monkeypatch.setattr(domain_tools, "activate_window", lambda *_a: None)
    monkeypatch.setattr(domain_tools.user32, "GetForegroundWindow", lambda: 1)
    monkeypatch.setattr(domain_tools, "reserve_text_input", lambda **_kwargs: True)
    monkeypatch.setattr(domain_tools, "_wait_stable", lambda *_a, **_k: asyncio.sleep(0))
    monkeypatch.setattr(domain_tools, "_refresh_semantic", lambda *_a: False)
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "remember_success", lambda *_a: None)
    monkeypatch.setattr(domain_tools, "_focused_input_state", lambda *_a, **_k: {
        "verified": True, "method": "uia-focused-input", "ref": "e1", "role": "Edit"
    })

    async def keyboard_type(_args):
        return json.dumps({"status": "ok", "method": "send_input"})

    checks = iter([{"matches": []}, {"matches": [{"text": "hello"}]}])
    pasted = []
    monkeypatch.setattr(domain_tools, "computer_keyboard_type", keyboard_type)
    monkeypatch.setattr(domain_tools, "find_text", lambda *_a, **_k: next(checks))
    monkeypatch.setattr(domain_tools, "paste_unicode_text", lambda text: pasted.append(text) or True)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "type", "text": "hello"}]
    }))
    assert result["status"] == "ok"
    assert result["results"][0]["method"] == "clipboard_paste"
    assert result["results"][0]["fallback_used"] is True
    assert pasted == ["hello"]


@pytest.mark.asyncio
async def test_visual_type_without_input_focus_stops_before_injection(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.update({"title": "QQ Music"}, "visual")
    context.info = lambda: WindowInfo(1, "QQ Music", "QQMusic", (0, 0, 500, 500))
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [])
    monkeypatch.setattr(domain_tools, "_focused_input_state", lambda *_a, **_k: {
        "verified": False, "method": "uia-win32-focus",
        "reason": "no_focused_editable_control",
    })
    monkeypatch.setattr(domain_tools, "reserve_text_input",
                        lambda **_k: pytest.fail("unfocused text must not be reserved"))
    monkeypatch.setattr(domain_tools, "computer_keyboard_type",
                        lambda _a: pytest.fail("unfocused text must not use SendInput"))
    monkeypatch.setattr(domain_tools, "paste_unicode_text",
                        lambda _text: pytest.fail("unfocused text must not use clipboard fallback"))

    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "type", "text": "周杰伦 枫"}],
    }))

    assert result["code"] == "input_focus_not_verified"
    assert result["blocked"] is False
    assert context.strategy_level == 1
    assert "mode=uia" in result["next_hint"]


@pytest.mark.asyncio
async def test_clipboard_restore_warning_preserves_verified_input(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.keyboard import ClipboardRestoreError
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.update({"title": "WebView"}, "uia")
    context.info = lambda: WindowInfo(1, "WebView", "Chrome_WidgetWin", (0, 0, 500, 500))
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [])
    monkeypatch.setattr(domain_tools.user32, "GetForegroundWindow", lambda: 1)
    monkeypatch.setattr(domain_tools, "reserve_text_input", lambda **_kwargs: True)
    monkeypatch.setattr(domain_tools, "_wait_stable", lambda *_a, **_k: asyncio.sleep(0))
    monkeypatch.setattr(domain_tools, "_refresh_semantic", lambda *_a: False)
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "remember_success", lambda *_a: None)
    monkeypatch.setattr(domain_tools, "_focused_input_state", lambda *_a, **_k: {
        "verified": True, "method": "uia-focused-input", "ref": "e1", "role": "Edit"
    })

    async def keyboard_type(_args):
        return json.dumps({"status": "ok", "method": "send_input"})

    checks = iter([{"matches": []}, {"matches": [{"text": "hello"}]}])
    monkeypatch.setattr(domain_tools, "computer_keyboard_type", keyboard_type)
    monkeypatch.setattr(domain_tools, "find_text", lambda *_a, **_k: next(checks))
    monkeypatch.setattr(
        domain_tools, "paste_unicode_text",
        lambda _text: (_ for _ in ()).throw(ClipboardRestoreError("clipboard_restore failed")),
    )

    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "type", "text": "hello"}],
    }))

    assert result["status"] == "ok"
    assert result["results"][0]["method"] == "clipboard_paste"
    assert result["results"][0]["fallback_used"] is True
    assert "clipboard_restore failed" in result["results"][0]["clipboard_warning"]


@pytest.mark.parametrize("transient", [-2147221036, 0x800401D4])
def test_clipboard_hresult_retries_signed_and_unsigned_failures(monkeypatch, transient):
    from reasonix_computer_use import keyboard

    results = iter([transient, 0])
    sleeps = []
    monkeypatch.setattr(keyboard.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert keyboard._check_hresult(lambda: next(results), "clipboard_test", retries=2) == 0
    assert sleeps == [0.04]


def test_clipboard_allocation_is_freed_when_open_fails(monkeypatch):
    import ctypes
    from types import SimpleNamespace

    from reasonix_computer_use import keyboard

    class Function:
        def __init__(self, implementation):
            self.implementation = implementation
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.implementation(*args)

    allocation = 123
    buffer = ctypes.create_string_buffer(64)
    freed = []
    kernel32 = SimpleNamespace(
        GlobalAlloc=Function(lambda *_args: allocation),
        GlobalLock=Function(lambda _handle: ctypes.addressof(buffer)),
        GlobalUnlock=Function(lambda _handle: True),
        GlobalFree=Function(lambda handle: freed.append(handle) or 0),
    )
    user32 = SimpleNamespace(
        SetClipboardData=Function(lambda *_args: allocation),
        EmptyClipboard=Function(lambda: True),
        CloseClipboard=Function(lambda: True),
    )
    monkeypatch.setattr(keyboard.ctypes, "windll", SimpleNamespace(
        kernel32=kernel32, user32=user32,
    ))
    monkeypatch.setattr(
        keyboard, "_open_clipboard",
        lambda: (_ for _ in ()).throw(OSError("clipboard busy")),
    )

    with pytest.raises(OSError, match="clipboard busy"):
        keyboard._set_clipboard_text("hello")
    assert freed == [allocation]


@pytest.mark.asyncio
async def test_unverified_input_stops_following_keypress(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    context = WindowContext("w1", 1)
    context.update({"title": "A"}, "uia")
    context.info = lambda: WindowInfo(1, "A", "A", (0, 0, 500, 500))
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _: context)
    monkeypatch.setattr(domain_tools, "list_windows", lambda: [])
    monkeypatch.setattr(domain_tools, "_wait_stable", lambda *_a, **_k: asyncio.sleep(0))
    monkeypatch.setattr(domain_tools, "activate_window", lambda *_a: None)
    monkeypatch.setattr(domain_tools.user32, "GetForegroundWindow", lambda: 1)
    monkeypatch.setattr(domain_tools, "reserve_text_input", lambda **_kwargs: True)
    monkeypatch.setattr(domain_tools, "find_text", lambda *_a, **_k: {"matches": []})
    calls = []

    async def keyboard_type(_args):
        calls.append("type")
        return json.dumps({"status": "ok", "method": "send_input"})

    monkeypatch.setattr(domain_tools, "computer_keyboard_type", keyboard_type)
    monkeypatch.setattr(domain_tools, "paste_unicode_text",
                        lambda _text: (_ for _ in ()).throw(OSError("clipboard unavailable")))
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "type", "text": "周杰伦"}, {"type": "press", "keys": ["ENTER"]}]
    }))
    assert result["code"] == "input_not_verified"
    assert calls == ["type"]


def test_environment_setup_requires_confirmation(monkeypatch, tmp_path):
    from reasonix_computer_use import environment_setup

    monkeypatch.setattr(environment_setup, "setup_root", lambda: tmp_path)
    monkeypatch.setattr(environment_setup, "missing_modules", lambda: ["rapidocr_onnxruntime"])
    result = environment_setup.start_environment_setup(False)
    assert result["status"] == "confirmation_required"
    assert result["missing"] == ["rapidocr_onnxruntime"]


def test_environment_setup_starts_background_worker(monkeypatch, tmp_path):
    from reasonix_computer_use import environment_setup

    class Process:
        pid = 4321

    monkeypatch.setattr(environment_setup, "setup_root", lambda: tmp_path)
    monkeypatch.setattr(environment_setup, "missing_modules", lambda: ["comtypes"])
    original_find_spec = environment_setup.importlib.util.find_spec
    monkeypatch.setattr(environment_setup.importlib.util, "find_spec",
                        lambda name: object() if name == "pip" else original_find_spec(name))
    monkeypatch.setattr(environment_setup.subprocess, "Popen", lambda *_args, **_kwargs: Process())
    result = environment_setup.start_environment_setup(True)
    assert result["status"] == "installing"
    assert result["pid"] == 4321
    assert result["poll_after_seconds"] == 3


def test_environment_worker_installs_only_fixed_dependencies(monkeypatch, tmp_path):
    from reasonix_computer_use import environment_setup

    class Completed:
        returncode = 0

    calls = []
    monkeypatch.setattr(environment_setup, "setup_root", lambda: tmp_path)
    monkeypatch.setattr(environment_setup, "missing_modules", lambda: [])
    monkeypatch.setattr(environment_setup.subprocess, "run",
                        lambda command, **kwargs: calls.append((command, kwargs)) or Completed())
    assert environment_setup.run_worker() == 0
    command = calls[0][0]
    assert command[-len(environment_setup.DEPENDENCIES):] == list(environment_setup.DEPENDENCIES)
    assert "--target" in command
    state = json.loads((tmp_path / "setup-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "ready"


@pytest.mark.asyncio
async def test_computer_system_exposes_setup_status(monkeypatch):
    from reasonix_computer_use import domain_tools

    calls = []
    monkeypatch.setattr(domain_tools, "wait_environment_status",
                        lambda seconds: calls.append(seconds) or {
                            "status": "installing", "ready": False,
                            "poll_after_seconds": 3, "log_tail": ["Downloading"]})
    result = json.loads(await domain_tools.computer_system({
        "operation": "setup_status", "params": {"wait_seconds": 20}}))
    assert result["status"] == "installing"
    assert result["poll_after_seconds"] == 3
    assert calls == [20.0]


@pytest.mark.asyncio
async def test_command_output_is_bounded(monkeypatch):
    from reasonix_computer_use import domain_tools

    class Completed:
        returncode = 0
        stdout = "x" * 9000
        stderr = ""

    monkeypatch.setattr(domain_tools.subprocess, "run", lambda *a, **k: Completed())
    result = json.loads(await domain_tools.computer_system({"operation": "command", "target": "where python"}))
    assert len(result["stdout"]) == 4000
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_shell_cannot_bypass_gui_executor_even_when_claimed_confirmed():
    from reasonix_computer_use.domain_tools import computer_system

    result = json.loads(await computer_system({
        "operation": "command", "target": "Add-Type; [Windows.Forms.SendKeys]::SendWait('x')",
        "params": {"confirmed": True}
    }))
    assert result["code"] == "gui_command_blocked"
    assert result["blocked"] is True


@pytest.mark.asyncio
async def test_shell_rejects_legacy_params_command_bypass():
    from reasonix_computer_use.domain_tools import computer_system

    result = json.loads(await computer_system({
        "operation": "command", "target": "",
        "params": {"command": "[Windows.Forms.SendKeys]::SendWait('x')", "confirmed": True}
    }))
    assert result["code"] == "command_argument_blocked"
    assert result["blocked"] is True


def test_cross_process_input_guard_blocks_recent_replay(monkeypatch, tmp_path):
    from reasonix_computer_use import input_guard

    monkeypatch.setattr(input_guard, "memory_dir", lambda: tmp_path)
    values = dict(app_identity="qqmusic", window_class="TXGuiFoundation",
                  state_hash="blank-search", target_ref="e1", text="周杰伦", now=1000.0)
    assert input_guard.reserve_text_input(**values) is True
    assert input_guard.reserve_text_input(**values) is False
    values["now"] = 1601.0
    assert input_guard.reserve_text_input(**values) is True


def test_input_guard_ignores_volatile_state_within_same_task(monkeypatch, tmp_path):
    from reasonix_computer_use import input_guard

    monkeypatch.setattr(input_guard, "memory_dir", lambda: tmp_path)
    values = dict(app_identity="music", window_class="WebView", target_ref="search",
                  text="周杰伦", task_id="task-a", now=1000.0)
    assert input_guard.reserve_text_input(state_hash="before-caret", **values) is True
    assert input_guard.reserve_text_input(state_hash="after-caret", **values) is False
    assert input_guard.reserve_text_input(
        state_hash="after-caret", **{**values, "task_id": "task-b"}) is True


def test_input_guard_fails_closed_when_lock_cannot_be_acquired(monkeypatch, tmp_path):
    from reasonix_computer_use import input_guard

    monkeypatch.setattr(input_guard, "memory_dir", lambda: tmp_path)
    monkeypatch.setattr(input_guard, "_acquire_lock", lambda *_a, **_k: None)
    assert input_guard.reserve_text_input(
        app_identity="app", window_class="Window", state_hash="s",
        target_ref="e1", text="same", now=1000.0) is False


@pytest.mark.asyncio
async def test_type_requires_target_window_to_remain_foreground(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 101)
    monkeypatch.setattr(domain_tools, "activate_window", lambda *_a: None)
    monkeypatch.setattr(domain_tools.user32, "GetForegroundWindow", lambda: 202)
    result = await domain_tools._execute(context, {"type": "type", "text": "x"})
    assert result["code"] == "focus_denied"


@pytest.mark.asyncio
async def test_read_only_command_rejects_pipeline_mutation():
    from reasonix_computer_use.domain_tools import computer_system

    result = json.loads(await computer_system({
        "operation": "command", "target": "Get-Process | Stop-Process", "params": {"confirmed": True}
    }))
    assert result["code"] in ("command_blocked", "command_not_read_only")


@pytest.mark.asyncio
async def test_read_only_command_rejects_recursive_disk_scan():
    from reasonix_computer_use.domain_tools import computer_system

    result = json.loads(await computer_system({
        "operation": "command", "target": "Get-ChildItem C:\\ -Recurse"
    }))
    assert result["code"] == "command_not_read_only"
    assert result["blocked"] is True


@pytest.mark.asyncio
async def test_file_write_requires_confirmation(tmp_path):
    from reasonix_computer_use.domain_tools import computer_system

    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    result = json.loads(await computer_system({
        "operation": "file", "target": str(source),
        "params": {"action": "copy", "destination": str(tmp_path / "b.txt")}
    }))
    assert result["code"] == "confirmation_required"


@pytest.mark.asyncio
async def test_file_search_uses_redirected_known_folders(monkeypatch, tmp_path):
    from reasonix_computer_use import domain_tools

    desktop = tmp_path / "Desktop"
    documents = tmp_path / "Documents"
    downloads = tmp_path / "Downloads"
    nested = documents / "Projects" / "Reasonix"
    for directory in (desktop, documents, downloads, nested):
        directory.mkdir(parents=True, exist_ok=True)
    wanted = nested / "季度报告.xlsx"
    wanted.write_bytes(b"xlsx")
    monkeypatch.setattr(domain_tools, "ensure_index", lambda: {"known_folders": {
        "桌面": {"path": str(desktop)}, "文档": {"path": str(documents)},
        "下载": {"path": str(downloads)},
    }})

    result = json.loads(await domain_tools.computer_system({
        "operation": "file", "params": {"action": "search", "query": "季度报告", "kind": "file"}
    }))
    assert result["status"] == "ok"
    assert result["result"]["matches"][0]["path"] == str(wanted.resolve())
    assert result["result"]["roots"] == [str(desktop.resolve()), str(documents.resolve()),
                                           str(downloads.resolve())]
    assert result["next_hint"].startswith("找到目标后")


@pytest.mark.asyncio
async def test_file_search_honors_depth_and_result_limits(tmp_path):
    from reasonix_computer_use import domain_tools

    shallow = tmp_path / "one"
    deep = shallow / "two"
    deep.mkdir(parents=True)
    (shallow / "match-one.txt").write_text("one", encoding="utf-8")
    (deep / "match-two.txt").write_text("two", encoding="utf-8")
    result = json.loads(await domain_tools.computer_system({
        "operation": "file", "target": str(tmp_path),
        "params": {"action": "search", "query": "match-*.txt", "kind": "file",
                   "max_depth": 1, "max_results": 1}
    }))
    assert result["status"] == "ok"
    assert result["result"]["count"] == 1
    assert result["result"]["matches"][0]["name"] == "match-one.txt"


@pytest.mark.asyncio
async def test_file_search_skips_unavailable_default_known_folder(monkeypatch, tmp_path):
    from reasonix_computer_use import domain_tools

    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    wanted = desktop / "notes.txt"
    wanted.write_text("notes", encoding="utf-8")
    monkeypatch.setattr(domain_tools, "ensure_index", lambda: {"known_folders": {
        "桌面": {"path": str(desktop)},
    }})
    result = json.loads(await domain_tools.computer_system({
        "operation": "file", "params": {"action": "search", "query": "notes.txt"}
    }))
    assert result["status"] == "ok"
    assert result["result"]["matches"][0]["path"] == str(wanted.resolve())


@pytest.mark.asyncio
async def test_file_search_rejects_drive_root():
    from reasonix_computer_use import domain_tools

    root = str(Path.cwd().anchor)
    result = json.loads(await domain_tools.computer_system({
        "operation": "file", "target": root,
        "params": {"action": "search", "query": "*.txt"}
    }))
    assert result["status"] == "error"
    assert "磁盘根目录" in result["message"]


def test_manifest_and_docs_reference_new_api():
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads((root / "reasonix-plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.8.0-beta.3"
    assert manifest["commands"] == ["commands"]
    assert set(manifest["hooks"]) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
    routing = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "/computer-use:run" in routing
    assert "computer_task_start" not in routing
    run_command = (root / "commands" / "run.md").read_text(encoding="utf-8")
    operator = (root / "agents" / "operator.md").read_text(encoding="utf-8")
    assert "禁止再次调用 `slash_command`" in run_command
    assert "禁止使用文件工具读取 `agents/operator.md`" in run_command
    assert "不要检查或枚举命令/Skill 列表" in operator
    input_reference = root / "skills" / "computer-use" / "references" / "input-actions.md"
    if not input_reference.is_file():
        import pytest
        pytest.skip("computer-use references not yet populated")
    assert "multi_click" in input_reference.read_text(encoding="utf-8")


def test_spreadsheet_skill_is_packaged_and_concise():
    import pytest
    root = Path(__file__).resolve().parent.parent
    # spreadsheet-control has been merged into computer-use
    skill = root / "skills" / "computer-use"
    if not skill.is_dir():
        pytest.skip("computer-use skill not found")
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    assert "select_cell" in text or "screen_interactor" in text
    assert len(text) < 5000


def test_route_guard_denies_computer_tool_without_explicit_activation(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    session = "s1"
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": session,
                        "prompt": "桌面新建Excel，然后使用计算器应用逐个相加并保存文件"})
    result = route_guard.handle({"hook_event_name": "PreToolUse", "session_id": session,
                                 "tool_name": "computer_app"})
    output = result["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "未显式激活" in output["permissionDecisionReason"]


def test_route_guard_allows_user_requested_python(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    session = "s2"
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": session,
                        "prompt": "使用Python脚本在桌面创建Excel文件"})
    assert route_guard.handle({"hook_event_name": "PreToolUse", "session_id": session,
                               "tool_name": "bash", "tool_input": {"command": "python task.py"}}) is None


def test_route_guard_does_not_affect_non_desktop_tasks(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    session = "s3"
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": session,
                        "prompt": "运行项目测试并修复失败"})
    assert route_guard.handle({"hook_event_name": "PreToolUse", "session_id": session,
                               "tool_name": "bash"}) is None


def test_route_guard_launcher_emits_utf8_deny(tmp_path):
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path))
    prompt = {"hook_event_name": "UserPromptSubmit", "session_id": "cli-test",
              "prompt": "/computer-use:run 桌面新建Excel并使用计算器应用逐个相加"}
    submitted = subprocess.run(["cmd", "/d", "/c", str(root / "reasonix-computer-use.bat"), "--hook"],
                               input=json.dumps(prompt, ensure_ascii=False).encode("utf-8"),
                               capture_output=True, env=env, timeout=10, check=True)
    assert json.loads(submitted.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
    assert "禁止调用 slash_command" in json.loads(
        submitted.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
    before = {"hook_event_name": "PreToolUse", "session_id": "other-task", "tool_name": "computer_app"}
    blocked = subprocess.run(["cmd", "/d", "/c", str(root / "reasonix-computer-use.bat"), "--hook"],
                             input=json.dumps(before).encode("utf-8"), capture_output=True,
                             env=env, timeout=10, check=True)
    assert json.loads(blocked.stdout.decode("utf-8"))["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_route_guard_without_session_id_fails_closed(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    route_guard.handle({"hook_event_name": "UserPromptSubmit",
                        "prompt": "/computer-use:run 打开WPS表格并修改单元格后保存文件"})
    result = route_guard.handle({"hook_event_name": "PreToolUse", "tool_name": "computer_app"})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "activation_context_missing" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_route_guard_uses_thread_id_to_isolate_tasks(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "thread_id": "gui",
                        "prompt": "/computer-use:run 打开WPS并点击单元格"})
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "thread_id": "code",
                        "prompt": "运行项目测试"})
    allowed = route_guard.handle({"hook_event_name": "PreToolUse", "thread_id": "gui",
                                  "tool_name": "computer_app"})
    blocked = route_guard.handle({"hook_event_name": "PreToolUse", "thread_id": "code",
                                  "tool_name": "computer_app"})
    assert allowed is None
    assert blocked["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_route_guard_marks_pending_completion_incomplete_on_stop(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    session = "playback-pending"
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": session,
                        "prompt": "/computer-use:run 打开QQ音乐并播放周杰伦的枫"})
    warning = route_guard.handle({
        "hook_event_name": "PostToolUse", "session_id": session,
        "tool_name": "mcp__computer-use__computer_state",
        "tool_result": {"status": "ok", "task_completion": {
            "status": "pending", "verified": False,
            "required": ["open", "playback"], "completed": ["open"],
            "pending": ["playback"],
        }},
    })
    assert "不得在最终回答中宣称完成" in warning["hookSpecificOutput"]["additionalContext"]
    stopped = route_guard.handle({"hook_event_name": "Stop", "session_id": session})
    assert "incomplete" in stopped["hookSpecificOutput"]["additionalContext"]


def test_route_guard_allows_stop_after_verified_completion(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    session = "playback-complete"
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": session,
                        "prompt": "/computer-use:run 播放周杰伦的枫"})
    route_guard.handle({
        "hook_event_name": "PostToolUse", "session_id": session,
        "tool_name": "mcp__computer-use__computer_action",
        "tool_result": {"status": "ok", "task_completion": {
            "status": "verified", "verified": True,
            "required": ["playback"], "completed": ["playback"], "pending": [],
        }},
    })
    assert route_guard.handle({"hook_event_name": "Stop", "session_id": session}) is None


def test_external_vision_cannot_complete_pending_gui_task(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    monkeypatch.setattr(route_guard, "_external_vision_tool_allowed", lambda _payload: True)
    session = "vision-is-perception"
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": session,
                        "prompt": "/computer-use:run 播放周杰伦的枫"})
    warning = route_guard.handle({
        "hook_event_name": "PostToolUse", "session_id": session,
        "tool_name": "mcp__mimo-mcp__understand_image",
        "tool_result": {"status": "ok", "description": "静态截图显示枫和暂停图标"},
    })
    assert "视觉结果只属于感知证据" in warning["hookSpecificOutput"]["additionalContext"]
    assert "computer_action" in warning["hookSpecificOutput"]["additionalContext"]


def test_readme_documents_git_dependencies_and_windows_release():
    root = Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "reasonix plugin install" in readme
    for dependency in ("Pillow", "comtypes", "paddleocr"):
        assert dependency in readme
    # Legacy README check: these are now in the old docs
    import pytest as _pytest
    if "windows-x64.zip" not in readme:
        _pytest.skip("legacy windows release line removed in beta.2")


def test_release_builder_uses_manifest_version_and_emits_checksum():
    root = Path(__file__).resolve().parent.parent
    script = (root / "scripts" / "build_release.ps1").read_text(encoding="utf-8")
    assert "ConvertFrom-Json" in script
    assert "$manifest.version" in script
    assert "windows-x64" in script
    assert "Get-FileHash" in script
    assert "pyproject.toml" in script
    assert "@dependencies" in script
    assert "0.8.0-alpha.0" not in script


def test_windows_release_workflow_builds_and_publishes_assets():
    root = Path(__file__).resolve().parent.parent
    workflow = (root / ".github" / "workflows" / "release-windows.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in workflow
    assert 'tags:' in workflow
    assert "build_release.ps1" in workflow
    assert "build_installer.ps1" in workflow
    assert "innosetup" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "gh @arguments" in workflow
    assert "dist/*.sha256" in workflow
    assert "dist/*.exe" in workflow
    assert "$assets = Get-ChildItem dist" in workflow


def test_windows_installer_is_user_scoped_and_registers_plugin():
    root = Path(__file__).resolve().parent.parent
    installer = (root / "installer" / "reasonix-computer-use.iss").read_text(encoding="utf-8")
    builder = (root / "scripts" / "build_installer.ps1").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in installer
    assert "{localappdata}\\ReasonixPlugins\\computer-use" in installer
    assert "reasonix plugin install" in installer
    assert "Inno Setup 6" in builder
    assert "Get-FileHash" in builder
