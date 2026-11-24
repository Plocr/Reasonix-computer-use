"""Regression tests for the post-review fix batch (0.9.0-preview audit).

Covers:
  - _resolve_vk refuses non-ASCII single-character keys
  - FfmpegRecorder restarts stop the previous subprocess instead of leaking
  - text_vision.find_text honors the image argument it receives
  - task_planner.generate_plan fills needs_dependencies
  - computer_system exposes the trace operation declared in its schema
  - web_navigator is no longer annotated read_only
  - hidden tools are wired to real implementations, not stubs
"""

from __future__ import annotations

import asyncio
import sys

import pytest


# ── Platform (Windows-only) fixes ───────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only VK table")
def test_resolve_vk_rejects_non_ascii_single_char():
    from reasonix_computer_use.platform.windows import _resolve_vk

    assert _resolve_vk("enter") == 0x0D
    assert _resolve_vk("a") == ord("A")
    with pytest.raises(ValueError):
        _resolve_vk("汉")
    with pytest.raises(ValueError):
        _resolve_vk("not-a-key")


def test_ffmpeg_recorder_restart_stops_previous(monkeypatch):
    from reasonix_computer_use.platform.common import FfmpegRecorder

    calls: list[list[str]] = []
    terminated: list[bool] = []

    class FakeProcess:
        def __init__(self, args, **kwargs):
            calls.append(args)

        def poll(self):
            return None  # still running

        def terminate(self):
            terminated.append(True)

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr("reasonix_computer_use.platform.common.subprocess.Popen",
                        FakeProcess)
    monkeypatch.setattr("reasonix_computer_use.platform.common.time.sleep",
                        lambda _s: None)  # skip the 0.6s probe

    recorder = FfmpegRecorder()
    out = pytest.importorskip("pathlib").Path("out.mp4")

    assert recorder.start(out, lambda _rect: ["ffmpeg", "out.mp4"]) is True
    assert recorder.start(out, lambda _rect: ["ffmpeg", "out2.mp4"]) is True
    # The first recording must have been stopped before the second started.
    assert terminated == [True]
    assert len(calls) == 2


# ── Legacy shim fixes ───────────────────────────────────────────────────────

def test_find_text_honors_image_argument(monkeypatch):
    import numpy as np
    from PIL import Image
    from reasonix_computer_use import text_vision

    captured: list[object] = []

    def fake_detect_text(image_bgr):
        captured.append(image_bgr)
        return [{"x": 10, "y": 20, "w": 30, "h": 12,
                 "text": "许嵩", "conf": 95.0}]

    monkeypatch.setattr(
        "reasonix_computer_use.perception.vision.easy_ocr.detect_text",
        fake_detect_text)

    img = Image.new("RGB", (64, 64), color="white")
    matches = text_vision.find_text(img, "许嵩")

    # The supplied image must be OCR'd — never silently replaced by a
    # full-screen observe.
    assert captured, "detect_text was not called with the provided image"
    assert isinstance(captured[0], np.ndarray)
    assert matches and matches[0]["text"] == "许嵩"
    assert matches[0]["center"] == [25, 26]


def test_generate_plan_fills_needs_dependencies(monkeypatch):
    from reasonix_computer_use import task_planner

    monkeypatch.setattr(
        "reasonix_computer_use.environment_setup.missing_modules",
        lambda: ["PIL"])
    plan = task_planner.generate_plan("打开记事本")
    assert plan.needs_dependencies == ["PIL"]
    assert "PIL" in plan.to_context_string()


# ── Tool registration fixes ─────────────────────────────────────────────────

def test_computer_system_trace_operation_registered():
    from reasonix_computer_use.tools.computer_system import ComputerSystem

    result = asyncio.run(ComputerSystem().trace(""))
    assert result["status"] == "ok"
    assert isinstance(result["traces"], list)


def test_web_navigator_not_read_only():
    from reasonix_computer_use import tools  # noqa: F401
    from reasonix_computer_use.mcp_server import TOOLS

    entry = TOOLS["web_navigator"]
    annotations = entry.get("annotations", {})
    assert not annotations.get("readOnlyHint"), \
        "web_navigator performs navigation/actions and must not be read-only"


def test_hidden_tool_schemas_are_real():
    from reasonix_computer_use import tools  # noqa: F401
    from reasonix_computer_use.mcp_server import TOOLS

    for name in ("mouse_action", "keyboard_action", "screenshot", "screen_recorder"):
        entry = TOOLS[name]
        properties = entry["inputSchema"].get("properties", {})
        assert properties, f"{name} must declare a real input schema"
        assert entry["description"].startswith("[HIDDEN]")


def test_hidden_keyboard_tool_executes(monkeypatch):
    import json
    from reasonix_computer_use import tools  # noqa: F401
    from reasonix_computer_use.mcp_server import TOOLS

    typed: list[str] = []

    class FakePlatform:
        def keyboard_type(self, text):
            typed.append(text)

    monkeypatch.setattr("reasonix_computer_use.platform.get_platform",
                        lambda: FakePlatform())
    monkeypatch.setattr("reasonix_computer_use.tools.hidden.actions.get_platform",
                        lambda: FakePlatform())

    result = json.loads(asyncio.run(
        TOOLS["keyboard_action"]["handler"]({"operation": "type", "text": "hi"})))
    assert result["status"] == "ok"
    assert typed == ["hi"]


def test_hidden_screenshot_tool_executes(monkeypatch, tmp_path):
    import json
    from PIL import Image
    from reasonix_computer_use import tools  # noqa: F401
    from reasonix_computer_use.mcp_server import TOOLS

    class FakePlatform:
        def screenshot(self, rect=None):
            return Image.new("RGB", (32, 32), color="red")

    monkeypatch.setattr("reasonix_computer_use.platform.get_platform",
                        lambda: FakePlatform())
    monkeypatch.setattr("reasonix_computer_use.tools.hidden.actions.get_platform",
                        lambda: FakePlatform())
    # Route the output into a temp dir as the fake "Downloads".
    monkeypatch.setattr("os.path.isdir", lambda _p: True)
    monkeypatch.setattr("os.path.expanduser", lambda _p: str(tmp_path))

    result = json.loads(asyncio.run(
        TOOLS["screenshot"]["handler"]({"filename": "shot.png"})))
    assert result["status"] == "ok"
    assert result["action"] == "screenshot"


# ── Manifest fixes ──────────────────────────────────────────────────────────

def test_manifest_declares_api_version():
    import json
    from pathlib import Path

    manifest = json.loads(
        (Path(__file__).parent.parent / "reasonix-plugin.json").read_text(encoding="utf-8"))
    assert manifest.get("apiVersion") == "reasonix.io/plugin/v2"
    for event, hooks in manifest.get("hooks", {}).items():
        assert hooks, f"{event} must declare hooks"
        for hook in hooks:
            # Exec form: args present means no shell parsing, no variable
            # expansion — the legacy silent shell behaviour is gone.
            assert isinstance(hook.get("args"), list), \
                f"{event} hook must use explicit exec form (args)"


def test_tomllib_import_paths():
    # The vision router is imported at MCP startup; on Python 3.10 the
    # tomli fallback keeps this importable.  On 3.11+ it uses tomllib.
    import reasonix_computer_use.vision_router  # noqa: F401

    from reasonix_computer_use.vision_router import resolve_vision_route
    route = resolve_vision_route(payload={}, environ={}, base_dir=None,
                                 config_paths=[])
    assert route.mode == "unavailable"  # fail closed with no declaration


def test_mcp_initialize_handshake():
    from reasonix_computer_use import __version__
    from reasonix_computer_use.mcp_server import handle_initialize

    response = asyncio.run(handle_initialize(1))
    result = response["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "reasonix-computer-use"
    assert result["serverInfo"]["version"] == __version__


def test_tools_list_excludes_hidden():
    from reasonix_computer_use import tools  # noqa: F401
    from reasonix_computer_use.mcp_server import handle_tools_list

    response = asyncio.run(handle_tools_list(1))
    names = {item["name"] for item in response["result"]["tools"]}
    assert "screen_interactor" in names
    assert "mouse_action" not in names
    assert "keyboard_action" not in names
    assert "screenshot" not in names
    assert "screen_recorder" not in names
