import json
from pathlib import Path

import pytest


def test_unknown_model_capability_fails_closed():
    from reasonix_computer_use.vision_router import resolve_vision_route

    route = resolve_vision_route(environ={}, config_paths=[])
    assert route.mode == "unavailable"
    assert route.available is False
    assert route.reason == "vision_capability_not_declared"


@pytest.mark.parametrize("payload", [
    {"imageInputEnabled": True},
    {"capabilities": {"supportsVision": True}},
    {"model": {"image_input_enabled": True, "name": "vision-model"}},
])
def test_explicit_host_capability_enables_native_images(payload):
    from reasonix_computer_use.vision_router import resolve_vision_route

    route = resolve_vision_route(payload, environ={}, config_paths=[])
    assert route.mode == "native"
    assert route.available is True


def test_reasonix_vision_models_are_resolved_without_name_guessing(tmp_path):
    from reasonix_computer_use.vision_router import resolve_vision_route

    config = tmp_path / "reasonix.toml"
    config.write_text("""
[[providers]]
name = "mimo"
models = ["mimo-v2.5", "mimo-v2.5-pro"]
default = "mimo-v2.5-pro"
vision_models = ["mimo-v2.5"]
""", encoding="utf-8")

    visual = resolve_vision_route(environ={"REASONIX_MODEL": "mimo/mimo-v2.5"},
                                  config_paths=[config])
    textual = resolve_vision_route(environ={"REASONIX_MODEL": "mimo/mimo-v2.5-pro"},
                                   config_paths=[config])
    assert (visual.mode, visual.provider, visual.reason) == (
        "native", "mimo", "model_declared_vision")
    assert textual.mode == "unavailable"
    assert textual.reason == "model_text_only"


def test_singular_provider_model_and_default_model_are_resolved(tmp_path):
    from reasonix_computer_use.vision_router import resolve_vision_route

    config = tmp_path / "reasonix.toml"
    config.write_text("""
default_model = "text-provider"
[[providers]]
name = "text-provider"
model = "text-model"
vision = false
""", encoding="utf-8")

    by_alias = resolve_vision_route(environ={"REASONIX_MODEL": "text-provider"},
                                    config_paths=[config])
    by_default = resolve_vision_route(environ={}, config_paths=[config])
    assert by_alias.reason == "model_text_only"
    assert by_default.reason == "model_text_only"


def test_explicit_text_only_override_wins_over_vision_model_list(tmp_path):
    from reasonix_computer_use.vision_router import resolve_vision_route

    config = tmp_path / "reasonix.toml"
    config.write_text("""
[[providers]]
name = "gateway"
models = ["mixed"]
vision = false
vision_models = ["mixed"]
""", encoding="utf-8")
    route = resolve_vision_route(environ={"REASONIX_MODEL": "gateway/mixed"},
                                 config_paths=[config])
    assert route.mode == "unavailable"
    assert route.reason == "model_text_only"


def test_contradictory_payload_capabilities_fail_closed():
    from reasonix_computer_use.vision_router import resolve_vision_route

    route = resolve_vision_route({
        "capabilities": {"supportsVision": True},
        "model": {"image_input_enabled": False},
    }, environ={}, config_paths=[])
    assert route.mode == "unavailable"


def test_contradictory_environment_capabilities_fail_closed():
    from reasonix_computer_use.vision_router import resolve_vision_route

    route = resolve_vision_route(environ={
        "REASONIX_IMAGE_INPUT_ENABLED": "true",
        "REASONIX_MODEL_SUPPORTS_VISION": "false",
    }, config_paths=[])
    assert route.mode == "unavailable"
    assert route.reason == "host_declared_text_only"


def test_native_route_cannot_override_explicit_text_only_model(tmp_path):
    from reasonix_computer_use.vision_router import resolve_vision_route

    config = tmp_path / "reasonix.toml"
    config.write_text("""
[[providers]]
name = "gateway"
models = ["text"]
vision = false
""", encoding="utf-8")
    route = resolve_vision_route(
        environ={"REASONIX_VISION_ROUTE": "native", "REASONIX_MODEL": "gateway/text"},
        config_paths=[config])
    assert route.mode == "unavailable"
    assert route.reason == "native_route_conflicts_with_text_only_model"


def test_official_mimo_fallback_matches_reasonix_config_logic(tmp_path):
    from reasonix_computer_use.vision_router import resolve_vision_route

    config = tmp_path / "reasonix.toml"
    config.write_text("""
[[providers]]
name = "mimo"
base_url = "https://api.xiaomimimo.com/v1"
models = ["mimo-v2.5"]
""", encoding="utf-8")
    route = resolve_vision_route(environ={"REASONIX_MODEL": "mimo/mimo-v2.5"},
                                 config_paths=[config])
    assert route.mode == "native"


def test_external_mcp_route_is_explicit_handoff():
    from reasonix_computer_use.vision_router import resolve_vision_route

    route = resolve_vision_route(environ={
        "REASONIX_VISION_MCP_SERVER": "mimo-mcp",
        "REASONIX_VISION_MCP_TOOL": "understand_image",
        "REASONIX_VISION_MODEL": "mimo-v2.5",
    }, config_paths=[])
    assert route.mode == "external"
    assert route.server == "mimo-mcp"
    assert route.tool == "understand_image"
    assert route.model == "mimo-v2.5"
    assert route.handoff == "agent"


def test_bare_mimo_server_uses_canonical_image_tool():
    from reasonix_computer_use.vision_router import resolve_vision_route

    route = resolve_vision_route(
        environ={"REASONIX_VISION_MCP_SERVER": "mimo-mcp"}, config_paths=[])
    assert route.mode == "external"
    assert route.tool == "understand_image"


@pytest.mark.parametrize("value", [
    {"server": "other-mcp"},
    {"tool": "understand_image"},
    "other-mcp",
    "mcp:other-mcp/",
])
def test_incomplete_external_route_is_not_marked_available(value):
    from reasonix_computer_use.vision_router import resolve_vision_route

    route = resolve_vision_route(
        environ={"REASONIX_VISION_ROUTE": json.dumps(value) if isinstance(value, dict) else value},
        config_paths=[])
    assert route.mode == "unavailable"
    assert route.available is False


def test_configured_mimo_mcp_is_discovered_as_text_model_fallback(tmp_path):
    from reasonix_computer_use.vision_router import resolve_vision_route

    config = tmp_path / "reasonix.toml"
    config.write_text("""
[[plugins]]
name = "mimo-mcp"
command = "npx"
args = ["mimo-mcp-server", "-y"]
env = { MIMO_API_KEY = "not-read-by-router" }
""", encoding="utf-8")
    route = resolve_vision_route(environ={}, config_paths=[config])
    assert route.mode == "external"
    assert route.server == "mimo-mcp"
    assert route.tool == "understand_image"
    serialized = json.dumps(route.as_dict())
    assert "not-read-by-router" not in serialized


def test_native_model_wins_over_configured_mimo_fallback(tmp_path):
    from reasonix_computer_use.vision_router import resolve_vision_route

    config = tmp_path / "reasonix.toml"
    config.write_text("""
[[providers]]
name = "native"
models = ["vision"]
vision_models = ["vision"]

[[plugins]]
name = "mimo-mcp"
command = "npx"
args = ["mimo-mcp-server", "-y"]
""", encoding="utf-8")
    route = resolve_vision_route(environ={"REASONIX_MODEL": "native/vision"},
                                 config_paths=[config])
    assert route.mode == "native"


def test_explicit_route_disable_prevents_automatic_mimo_handoff(tmp_path):
    from reasonix_computer_use.vision_router import resolve_vision_route

    config = tmp_path / "reasonix.toml"
    config.write_text("""
[[plugins]]
name = "mimo-mcp"
command = "npx"
args = ["mimo-mcp-server", "-y"]
""", encoding="utf-8")
    route = resolve_vision_route(environ={"REASONIX_VISION_ROUTE": "none"},
                                 config_paths=[config])
    assert route.mode == "unavailable"
    assert route.reason == "explicit_route_disabled"


def test_external_route_file_is_supported(tmp_path):
    from reasonix_computer_use.vision_router import resolve_vision_route

    route_file = tmp_path / "vision.json"
    route_file.write_text(json.dumps({
        "mode": "external", "provider": "mimo", "server": "mimo-mcp",
        "tool": "understand_image", "model": "mimo-v2.5",
    }), encoding="utf-8")
    route = resolve_vision_route(
        environ={"REASONIX_VISION_ROUTE_FILE": str(route_file)}, config_paths=[])
    assert route.mode == "external"
    assert route.provider == "mimo"


def test_mcp_boundary_replaces_visual_result_for_text_model(monkeypatch):
    from reasonix_computer_use import mcp_server
    from reasonix_computer_use.vision_router import VisionRoute

    monkeypatch.setattr(mcp_server, "resolve_vision_route", lambda **_kwargs: VisionRoute(
        "unavailable", False, "test", "host_declared_text_only", model="text-only"))
    original = json.dumps({
        "status": "ok", "source": "visual", "revision": "r3",
        "image_path": "should-not-be-forwarded.png", "visual_count": 1,
        "window": {"id": "w1"},
    })
    guarded = json.loads(mcp_server._guard_visual_result(
        "computer_state", original, {"goal": "点击播放按钮"}))
    assert guarded["status"] == "error"
    assert guarded["code"] == "vision_unavailable"
    assert guarded["blocked"] is True
    assert guarded["attempted_source"] == "visual"
    assert "image_path" not in guarded


def test_mcp_boundary_returns_external_handoff_metadata(monkeypatch):
    from reasonix_computer_use import mcp_server
    from reasonix_computer_use.vision_router import VisionRoute

    monkeypatch.setattr(mcp_server, "resolve_vision_route", lambda **_kwargs: VisionRoute(
        "external", True, "test", "external_route_configured", model="mimo-v2.5",
        server="mimo-mcp", tool="understand_image", handoff="agent"))
    original = json.dumps({
        "status": "ok", "source": "visual", "revision": "r4",
        "image_path": "capture-r4.png",
    })
    guarded = json.loads(mcp_server._guard_visual_result(
        "computer_state", original, {"goal": "点击播放按钮"}))
    assert guarded["code"] == "vision_handoff_required"
    assert guarded["blocked"] is False
    assert guarded["retryable"] is True
    assert guarded["image_path"] == "capture-r4.png"
    assert guarded["vision"]["server"] == "mimo-mcp"
    assert guarded["vision"]["tool"] == "understand_image"
    assert guarded["handoff_request"]["tool"] == "mcp__mimo-mcp__understand_image"
    assert guarded["handoff_request"]["arguments"]["images"] == ["capture-r4.png"]
    assert "点击播放按钮" in guarded["handoff_request"]["arguments"]["question"]
    assert "[left,top,right,bottom]" in guarded["handoff_request"]["arguments"]["question"]
    assert "uncertain" in guarded["handoff_request"]["arguments"]["question"]


def test_mcp_boundary_keeps_visual_result_for_native_model(monkeypatch):
    from reasonix_computer_use import mcp_server
    from reasonix_computer_use.vision_router import VisionRoute

    monkeypatch.setattr(mcp_server, "resolve_vision_route", lambda **_kwargs: VisionRoute(
        "native", True, "test", "host_declared_image_input", model="vision-model"))
    original = json.dumps({
        "status": "ok", "source": "visual", "revision": "r5", "image_path": "image.png",
    })
    guarded = json.loads(mcp_server._guard_visual_result("computer_state", original))
    assert guarded["status"] == "ok"
    assert guarded["source"] == "visual"
    assert guarded["image_path"] == "image.png"
    assert guarded["vision"]["mode"] == "native"


def test_incomplete_external_route_is_terminal_in_unavailable_result():
    from reasonix_computer_use.vision_router import VisionRoute, unavailable_result

    result = unavailable_result(VisionRoute(
        "external", True, "test", "bad", server="mimo-mcp", tool=""))
    assert result["code"] == "vision_unavailable"
    assert result["blocked"] is True


@pytest.mark.asyncio
async def test_mcp_does_not_trust_tool_arguments_for_vision(monkeypatch, tmp_path):
    from reasonix_computer_use import mcp_server
    from reasonix_computer_use import tools  # noqa: F401
    from reasonix_computer_use.vision_router import VisionRoute

    image = tmp_path / "window.png"
    image.write_bytes(b"png-data")

    async def handler(_args):
        return json.dumps({"status": "ok", "source": "visual",
                           "image_path": str(image), "revision": "r1"})

    seen = {}
    def resolver(*, payload=None, **_kwargs):
        seen.update(payload or {})
        return VisionRoute("unavailable", False, "test", "text_only")

    monkeypatch.setattr(mcp_server, "resolve_vision_route", resolver)
    monkeypatch.setitem(mcp_server.TOOLS["computer_state"], "handler", handler)
    result = await mcp_server.handle_tools_call(1, {
        "name": "computer_state",
        "arguments": {"imageInputEnabled": True, "capabilities": {"supportsVision": True}},
    })
    payload = json.loads(result["result"]["content"][0]["text"])
    assert payload["code"] == "vision_unavailable"
    assert seen == {}
    assert [item["type"] for item in result["result"]["content"]] == ["text"]


@pytest.mark.asyncio
async def test_mcp_static_visual_default_does_not_prove_live_call_model(monkeypatch, tmp_path):
    from reasonix_computer_use import mcp_server, tools  # noqa: F401
    from reasonix_computer_use.vision_router import VisionRoute

    image = tmp_path / "window.png"
    image.write_bytes(b"png-data")

    async def handler(_args):
        return json.dumps({"status": "ok", "source": "visual",
                           "image_path": str(image), "revision": "r1"})

    routes = iter([
        VisionRoute("native", True, "config", "model_declared_vision"),
        VisionRoute("unavailable", False, "explicit", "host_declared_text_only"),
    ])
    monkeypatch.setattr(mcp_server, "resolve_vision_route", lambda **_kwargs: next(routes))
    monkeypatch.setitem(mcp_server.TOOLS["computer_state"], "handler", handler)

    result = await mcp_server.handle_tools_call(1, {
        "name": "computer_state", "arguments": {},
    })
    payload = json.loads(result["result"]["content"][0]["text"])
    assert payload["code"] == "vision_unavailable"
    assert [item["type"] for item in result["result"]["content"]] == ["text"]


@pytest.mark.skip(reason="legacy: domain_tools/runtime removed in 0.8.0-beta.4 refactor; "
                         "fail-closed behavior covered by the vision_router tests above")
@pytest.mark.asyncio
async def test_unavailable_route_stops_before_window_capture(monkeypatch):
    from reasonix_computer_use import domain_tools
    from reasonix_computer_use.runtime import WindowContext
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(1, "Canvas", "Canvas", (0, 0, 400, 300), 10, "canvas.exe")
    context = WindowContext("w1", 1)
    context.update({"title": "Canvas"}, "window")
    context.info = lambda: info
    monkeypatch.setattr(domain_tools.REGISTRY, "get", lambda _window_id: context)
    monkeypatch.setattr(domain_tools, "observe", lambda *_a, **_k: {"elements": []})
    monkeypatch.setattr(domain_tools, "_ocr_elements", lambda *_a, **_k: ([], {
        "engine": "rapid", "available": True, "stable": True, "relevant": 0,
    }))
    monkeypatch.setattr(
        domain_tools, "_capture_window",
        lambda *_a, **_k: pytest.fail("unavailable vision must not capture a screenshot"),
    )

    result = json.loads(await domain_tools.computer_state({
        "window_id": "w1", "goal": "点击无文字图标",
        "task_goal": "点击无文字图标", "mode": "auto",
        "_mcp_vision_route": {
            "mode": "unavailable", "available": False,
            "source": "test", "reason": "host_declared_text_only",
        },
    }))
    assert result["status"] == "error"
    assert result["code"] == "vision_unavailable"
    assert context.visual_count == 0


def test_mcp_image_content_is_attached_only_for_native_route():
    from reasonix_computer_use.mcp_server import _should_attach_image

    assert _should_attach_image({
        "status": "ok", "source": "visual", "vision": {"mode": "native"},
    }) is True
    assert _should_attach_image({
        "status": "error", "source": "none", "image_path": "capture.png",
        "vision": {"mode": "external"},
    }) is False
    assert _should_attach_image({
        "status": "error", "source": "none", "vision": {"mode": "unavailable"},
    }) is False


def test_explicit_visual_call_is_denied_before_tool_for_text_model(monkeypatch, tmp_path):
    from hooks import route_guard
    from reasonix_computer_use.vision_router import VisionRoute

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    monkeypatch.setattr(route_guard, "resolve_vision_route", lambda _payload, **_kwargs: VisionRoute(
        "unavailable", False, "test", "host_declared_text_only"))
    route_guard.handle({
        "hook_event_name": "UserPromptSubmit", "session_id": "vision-session",
        "prompt": "/computer-use:run 点击无文字图标",
    })
    denied = route_guard.handle({
        "hook_event_name": "PreToolUse", "session_id": "vision-session",
        "tool_name": "mcp__computer-use__computer_state",
        "tool_input": {"window_id": "w1", "mode": "visual", "goal": "点击图标"},
    })
    output = denied["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert output["permissionDecisionReason"] == "vision_unavailable"


def test_session_start_stays_compact_and_activation_declares_visual_rule(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    monkeypatch.setattr(route_guard, "compact_route", lambda _payload, **_kwargs: {
        "mode": "unavailable", "available": False, "reason": "test"})
    result = route_guard.handle({"hook_event_name": "SessionStart", "session_id": "s1"})
    context = result["hookSpecificOutput"]["additionalContext"]
    assert "Computer Use 默认禁用" in context
    assert "vision_unavailable" not in context
    activated = route_guard.handle({
        "hook_event_name": "UserPromptSubmit", "session_id": "s1",
        "prompt": "/computer-use:run 点击无文字图标",
    })
    activated_context = activated["hookSpecificOutput"]["additionalContext"]
    assert "vision_unavailable" in activated_context
    assert "image placeholder" in activated_context


def test_hook_allows_only_the_configured_external_vision_tool(monkeypatch, tmp_path):
    from hooks import route_guard
    from reasonix_computer_use.vision_router import VisionRoute

    route = VisionRoute(
        "external", True, "test", "external_route_configured", server="mimo-mcp",
        tool="understand_image", handoff="agent")
    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    monkeypatch.setattr(route_guard, "resolve_vision_route", lambda _payload, **_kwargs: route)
    route_guard.handle({
        "hook_event_name": "UserPromptSubmit", "session_id": "handoff-session",
        "prompt": "/computer-use:run 点击无文字图标",
    })
    allowed = route_guard.handle({
        "hook_event_name": "PreToolUse", "session_id": "handoff-session",
        "tool_name": "mcp__mimo-mcp__understand_image",
        "tool_input": {"path": "capture.png"},
    })
    denied = route_guard.handle({
        "hook_event_name": "PreToolUse", "session_id": "handoff-session",
        "tool_name": "mcp__other__understand_image",
        "tool_input": {"path": "capture.png"},
    })
    assert allowed is None
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
