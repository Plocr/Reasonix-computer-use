"""Contract and unit tests for the 0.8 Reasonix domain API."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


PUBLIC_TOOLS = {"computer_app", "computer_state", "computer_action", "computer_system"}


def test_only_four_domain_tools_are_public():
    from reasonix_computer_use import tools  # noqa: F401
    from reasonix_computer_use.mcp_server import TOOLS

    assert set(TOOLS) == PUBLIC_TOOLS
    for tool in TOOLS.values():
        assert len(tool["description"]) > 20
        assert tool["inputSchema"]["type"] == "object"
        assert asyncio.iscoroutinefunction(tool["handler"])


def test_action_schema_exposes_canonical_type_and_coordinate_space():
    from reasonix_computer_use import tools  # noqa: F401
    from reasonix_computer_use.mcp_server import TOOLS

    item = TOOLS["computer_action"]["inputSchema"]["properties"]["actions"]["items"]
    assert item["required"] == ["type"]
    assert "click_ref" in item["properties"]["type"]["enum"]
    assert item["properties"]["coordinate_space"]["default"] == "window"


@pytest.mark.asyncio
async def test_mcp_initialize_and_list_report_08():
    from reasonix_computer_use.mcp_server import handle_initialize, handle_tools_list

    initialized = await handle_initialize(1)
    assert initialized["result"]["serverInfo"]["version"] == "0.8.0-alpha.2"
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


def test_switching_perception_channel_does_not_create_fake_revision():
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "A", "elements": ["button"]}, "uia")
    revision = context.revision
    assert context.update({"window": "A", "texts": ["button"]}, "ocr") is False
    assert context.revision == revision
    assert context.update({"window": "A", "texts": ["button"]}, "ocr") is False
    assert context.update({"window": "A", "texts": ["new"]}, "ocr") is True


def test_two_same_state_failures_escalate_strategy():
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "QQ"}, "uia")
    assert context.fail() == 1
    assert context.fail() == 2
    assert context.strategy_level == 2


def test_two_invalid_actions_trip_window_circuit_breaker():
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "QQ音乐"}, "uia")
    assert context.invalid_action() is False
    assert context.invalid_action() is True
    assert context.hard_blocked is True


def test_third_observation_without_action_trips_circuit_breaker():
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.update({"title": "QQ音乐"}, "uia")
    assert context.state_read() is False
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


def test_uia_walk_uses_created_true_condition(monkeypatch):
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
    result = json.loads(await domain_tools.computer_state({"window_id": "w1", "goal": "打开设置"}))
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
    result = json.loads(await domain_tools.computer_state({"window_id": "w1", "goal": "打开设置"}))
    assert result["source"] == "ocr"


@pytest.mark.asyncio
async def test_mcp_state_attaches_only_returned_window_image(tmp_path, monkeypatch):
    from reasonix_computer_use.mcp_server import TOOLS, handle_tools_call

    image = tmp_path / "window.png"
    image.write_bytes(b"png-data")

    async def handler(_args):
        return json.dumps({"status": "ok", "source": "visual", "image_path": str(image)})

    monkeypatch.setitem(TOOLS["computer_state"], "handler", handler)
    result = await handle_tools_call(1, {"name": "computer_state", "arguments": {}})
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
    first = json.loads(await domain_tools.computer_state({"window_id": "w1", "goal": "图标"}))
    second = json.loads(await domain_tools.computer_state({"window_id": "w1", "goal": "图标"}))
    assert first["source"] == "visual"
    assert second["source"] == "none"
    assert second["blocked"] is True
    assert len(captures) == 1


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


def test_uninstaller_is_not_a_launch_target(tmp_path):
    from reasonix_computer_use.system_index import _launchable_executable

    uninstaller = tmp_path / "unins000.exe"
    uninstaller.touch()
    app = tmp_path / "Ollama App.exe"
    app.touch()
    assert _launchable_executable(str(uninstaller)) is False
    assert _launchable_executable(str(app)) is True


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
    monkeypatch.setattr(domain_tools, "window_payload", lambda *a, **k: {"id": "w1"})
    result = json.loads(await domain_tools.computer_app({"operation": "launch", "app_id": "notepad"}))
    assert result["status"] == "ok"
    assert result["app"]["id"] == "real-id"


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
    monkeypatch.setattr(domain_tools, "_wait_stable", lambda *_a, **_k: asyncio.sleep(0))
    monkeypatch.setattr(domain_tools, "_refresh_semantic", lambda *_a: False)
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    monkeypatch.setattr(domain_tools, "remember_success", lambda *_a: None)

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
    monkeypatch.setattr(domain_tools, "find_text", lambda *_a, **_k: {"matches": []})
    calls = []

    async def keyboard_type(_args):
        calls.append("type")
        return json.dumps({"status": "ok", "method": "send_input"})

    monkeypatch.setattr(domain_tools, "computer_keyboard_type", keyboard_type)
    result = json.loads(await domain_tools.computer_action({
        "window_id": "w1", "revision": context.revision,
        "actions": [{"type": "type", "text": "周杰伦"}, {"type": "press", "keys": ["ENTER"]}]
    }))
    assert result["code"] == "input_not_verified"
    assert calls == ["type"]


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


def test_manifest_and_docs_reference_new_api():
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads((root / "reasonix-plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.8.0-alpha.2"
    assert "SessionStart" in manifest["hooks"]
    routing = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "chrome-devtools" in routing
    assert "computer_task_start" not in routing
