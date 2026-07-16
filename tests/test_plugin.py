"""Contract and unit tests for the 0.8 Reasonix domain API."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
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
    assert "select_cell" in item["properties"]["type"]["enum"]
    assert "select_range" in item["properties"]["type"]["enum"]
    assert "save_as" in item["properties"]["type"]["enum"]
    assert "cell" in item["properties"]
    assert item["properties"]["coordinate_space"]["default"] == "window"


@pytest.mark.asyncio
async def test_mcp_initialize_and_list_report_08():
    from reasonix_computer_use.mcp_server import handle_initialize, handle_tools_list

    initialized = await handle_initialize(1)
    assert initialized["result"]["serverInfo"]["version"] == "0.8.0-alpha.12"
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
    monkeypatch.setattr(domain_tools, "window_payload", lambda *a, **k: {"id": "w1"})
    result = json.loads(await domain_tools.computer_app({"operation": "launch", "app_id": "notepad"}))
    assert result["status"] == "ok"
    assert result["app"]["id"] == "real-id"


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
    monkeypatch.setattr(domain_tools, "window_payload", lambda *_a, **_k: {"id": "w1"})
    result = json.loads(await domain_tools.computer_app({"operation": "open_file",
                                                          "path": str(document)}))
    assert result["status"] == "ok"
    assert result["window"]["id"] == "w1"


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
async def test_ocr_ref_clicks_its_own_rectangle(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

    context = WindowContext("w1", 1)
    context.elements = [{"ref": "o15", "role": "text", "name": "百度",
                         "rect": [100, 200, 300, 240]}]
    clicks = []

    async def click(args):
        clicks.append(args)
        return json.dumps({"status": "ok"})

    monkeypatch.setattr(domain_tools, "computer_mouse_click", click)
    result = await domain_tools._execute(context, {"type": "click_text", "ref": "o15"})
    assert result["status"] == "ok"
    assert clicks[0]["x"] == 200
    assert clicks[0]["y"] == 220


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

    context = WindowContext("w1", 1)
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
    assert clicks == [{"x": 240, "y": 365, "button": "left"}]


def test_press_accepts_combined_shortcut_shape():
    from reasonix_computer_use.domain_tools import _press_parts

    assert _press_parts(["CTRL+L"]) == ("L", ["CTRL"])
    assert _press_parts(["ALT", "D"]) == ("D", ["ALT"])


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
async def test_select_cell_uses_spreadsheet_go_to(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext

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
    result = await domain_tools._execute(WindowContext("w1", 1),
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
    result = await domain_tools._execute(WindowContext("w1", 1),
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


def test_navigation_shortcuts_require_change_but_copy_does_not():
    from reasonix_computer_use.domain_tools import _requires_observable_change

    assert _requires_observable_change({"type": "press", "keys": ["CTRL", "O"]}) is True
    assert _requires_observable_change({"type": "press", "keys": ["CTRL", "C"]}) is False
    assert _requires_observable_change({"type": "click_ref", "ref": "e1"}) is True


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


def test_manifest_and_docs_reference_new_api():
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads((root / "reasonix-plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.8.0-alpha.12"
    assert manifest["commands"] == ["commands"]
    assert set(manifest["hooks"]) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
    routing = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "chrome-devtools" in routing
    assert "computer_task_start" not in routing


def test_spreadsheet_skill_is_packaged_and_concise():
    root = Path(__file__).resolve().parent.parent
    skill = root / "skills" / "spreadsheet-control"
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    reference = (skill / "references" / "shortcuts.md").read_text(encoding="utf-8")
    metadata = (skill / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert "select_cell" in text and "select_range" in text
    assert "不得按 F5" in text
    assert "support.office.com" in reference and "club.excelhome.net" in reference
    assert "$spreadsheet-control" in metadata
    assert len(text) < 5000


def test_route_guard_blocks_shell_for_explicit_gui_workflow(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    session = "s1"
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": session,
                        "prompt": "桌面新建Excel，然后使用计算器应用逐个相加并保存文件"})
    result = route_guard.handle({"hook_event_name": "PreToolUse", "session_id": session,
                                 "tool_name": "bash", "tool_input": {"command": "python task.py"}})
    output = result["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "computer-use" in output["permissionDecisionReason"]


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
              "prompt": "桌面新建Excel并使用计算器应用逐个相加"}
    submitted = subprocess.run(["cmd", "/d", "/c", str(root / "reasonix-computer-use.bat"), "--hook"],
                               input=json.dumps(prompt, ensure_ascii=False).encode("utf-8"),
                               capture_output=True, env=env, timeout=10, check=True)
    assert json.loads(submitted.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
    before = {"hook_event_name": "PreToolUse", "session_id": "cli-test", "tool_name": "bash"}
    blocked = subprocess.run(["cmd", "/d", "/c", str(root / "reasonix-computer-use.bat"), "--hook"],
                             input=json.dumps(before).encode("utf-8"), capture_output=True,
                             env=env, timeout=10, check=True)
    assert json.loads(blocked.stdout.decode("utf-8"))["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_route_guard_without_session_id_uses_current_task(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    route_guard.handle({"hook_event_name": "UserPromptSubmit",
                        "prompt": "打开WPS表格并修改单元格后保存文件"})
    result = route_guard.handle({"hook_event_name": "PreToolUse", "tool_name": "bash"})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_route_guard_uses_thread_id_to_isolate_tasks(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "thread_id": "gui",
                        "prompt": "打开WPS并点击单元格"})
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "thread_id": "code",
                        "prompt": "运行项目测试"})
    blocked = route_guard.handle({"hook_event_name": "PreToolUse", "thread_id": "gui",
                                  "tool_name": "bash"})
    allowed = route_guard.handle({"hook_event_name": "PreToolUse", "thread_id": "code",
                                  "tool_name": "bash"})
    assert blocked["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert allowed is None


def test_readme_documents_git_dependencies_and_windows_release():
    root = Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "git:github.com/Plocr/Reasonix-computer-use" in readme
    for dependency in ("Pillow", "comtypes", "rapidocr-onnxruntime"):
        assert dependency in readme
    assert "windows-x64.zip" in readme
    assert "windows-x64-setup.exe" in readme
    assert "无需安装 Python" in readme


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
