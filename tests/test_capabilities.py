from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import pytest


def test_hook_explicit_activation_and_ordinary_prompt_cleanup(monkeypatch, tmp_path):
    from hooks import route_guard
    from reasonix_computer_use import trace

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path / "hooks")
    monkeypatch.setattr("reasonix_computer_use.trace.memory_dir", lambda: tmp_path / "memory")

    session = "operator"
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": session,
                        "prompt": "/computer-use:run 打开计算器"})
    state = route_guard._read_state(route_guard._session_key({"session_id": session}))
    trace_id = state["trace_id"]
    assert state["enabled"] is True
    assert state["activation_source"] == "run"
    assert route_guard.handle({"hook_event_name": "PreToolUse", "session_id": session,
                               "tool_name": "computer_app"}) is None
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": session,
                        "prompt": "解释一段代码"})
    denied = route_guard.handle({"hook_event_name": "PreToolUse", "session_id": session,
                                 "tool_name": "computer_app"})
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    document = trace.read_trace(trace_id)
    assert document["events"][-1]["event"] == "task_end"
    assert document["events"][-1]["data"]["status"] == "cancelled"


def test_hook_blocked_stops_active_operator(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path / "hooks")
    monkeypatch.setattr("reasonix_computer_use.trace.memory_dir", lambda: tmp_path / "memory")
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": "p",
                        "prompt": "/computer-use:run 打开WPS编辑表格"})
    stopped = route_guard.handle({"hook_event_name": "PostToolUse", "session_id": "p",
                                  "tool_name": "computer_state",
                                  "tool_result": {"status": "ok", "blocked": True}})
    assert route_guard._read_state(route_guard._session_key({"session_id": "p"}))["blocked_seen"] is True
    assert "blocked" in stopped["hookSpecificOutput"]["additionalContext"]
    denied_repeat = route_guard.handle({"hook_event_name": "PreToolUse", "session_id": "p",
                                        "tool_name": "computer_app"})
    assert denied_repeat["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_reads_nested_mcp_content_result(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path / "hooks")
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": "nested",
                        "prompt": "/computer-use:run 点击图标"})
    nested = route_guard.handle({
        "hook_event_name": "PostToolUse", "session_id": "nested",
        "tool_name": "computer_state",
        "tool_result": {"content": [{"type": "text", "text":
            '{"status":"error","code":"vision_unavailable","blocked":true}'}]},
    })
    state = route_guard._read_state(route_guard._session_key({"session_id": "nested"}))
    assert state["blocked_seen"] is True
    assert "blocked" in nested["hookSpecificOutput"]["additionalContext"]



def test_hook_trace_captures_failed_tool_and_task_end(monkeypatch, tmp_path):
    from hooks import route_guard
    from reasonix_computer_use import trace

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path / "hooks")
    monkeypatch.setattr(trace, "memory_dir", lambda: tmp_path / "memory")
    monkeypatch.setattr(trace, "_read_index", lambda: {})
    session = "trace-task"
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": session,
                        "prompt": "/computer-use:run 打开音乐应用播放歌曲"})
    route_guard.handle({"hook_event_name": "PostToolUse", "session_id": session,
                        "tool_name": "computer_state", "tool_result": {
                            "status": "error", "code": "unknown_window", "blocked": True}})
    state = route_guard._read_state(route_guard._session_key({"session_id": session}))
    route_guard.handle({"hook_event_name": "Stop", "session_id": session})
    document = trace.read_trace(state["trace_id"])
    verification = next(item for item in document["events"] if item["event"] == "verification")
    assert verification["data"]["code"] == "unknown_window"
    assert verification["data"]["blocked"] is True
    assert document["events"][-1]["event"] == "task_end"
    assert document["events"][-1]["data"]["status"] == "blocked"


@pytest.mark.skip(reason="legacy: runtime.WindowRegistry removed in 0.8.0-beta.4 refactor")
def test_window_id_survives_registry_restart(monkeypatch):
    from reasonix_computer_use import runtime
    from reasonix_computer_use.windows import WindowInfo

    info = WindowInfo(0x1234AB, "Synthetic", "TestWindow", (0, 0, 800, 600), 42,
                      r"C:\Synthetic\app.exe")
    monkeypatch.setattr(runtime, "get_window_info", lambda hwnd: info if hwnd == info.hwnd else None)
    first = runtime.WindowRegistry().register(info, {"id": "synthetic", "name": "Synthetic"})
    assert first.window_id == "w-1234ab-2a-synthetic"
    recovered = runtime.WindowRegistry().get(first.window_id)
    assert recovered.hwnd == info.hwnd
    assert recovered.window_id == first.window_id


@pytest.mark.skip(reason="legacy: runtime/system_index removed in 0.8.0-beta.4 refactor")
def test_window_id_recovers_replaced_launcher_window(monkeypatch):
    from reasonix_computer_use import runtime, system_index
    from reasonix_computer_use.windows import WindowInfo

    replacement = WindowInfo(0x2222, "Synthetic", "MainWindow", (0, 0, 900, 700), 99,
                             r"C:\Synthetic\app.exe")
    monkeypatch.setattr(runtime, "get_window_info", lambda _hwnd: (_ for _ in ()).throw(ValueError("gone")))
    monkeypatch.setattr(runtime, "list_windows", lambda: [replacement])
    monkeypatch.setattr(system_index, "find_app", lambda app_id: {
        "id": app_id, "name": "Synthetic", "path": r"C:\Synthetic\app.exe"})
    recovered = runtime.WindowRegistry().get("w-1111-2a-synthetic")
    assert recovered.hwnd == replacement.hwnd
    assert recovered.owner_pid == replacement.pid
    assert recovered.app_id == "synthetic"


def test_mcp_source_signature_detects_plugin_update(tmp_path):
    from reasonix_computer_use.mcp_server import _source_signature

    source = tmp_path / "runtime.py"
    source.write_text("old", encoding="utf-8")
    before = _source_signature([source])
    source.write_text("updated source", encoding="utf-8")
    assert _source_signature([source]) != before


def test_trace_redacts_text_paths_and_secrets(monkeypatch, tmp_path):
    from reasonix_computer_use import trace

    monkeypatch.setattr(trace, "memory_dir", lambda: tmp_path)
    monkeypatch.setattr(trace, "_read_index", lambda: {"known_folders": {
        "桌面": {"path": "F:\\桌面"}}})
    trace_id = trace.start_trace(metadata={"goal": "private goal"})
    trace.record_event(trace_id, "action", {
        "text": "secret input", "password": "hunter2", "path": "F:\\桌面\\private.xlsx",
        "type": "type", "revision": "r1"})
    document = trace.read_trace(trace_id)
    serialized = json.dumps(document, ensure_ascii=False)
    assert "secret input" not in serialized
    assert "hunter2" not in serialized
    assert "F:\\\\桌面" not in serialized
    assert "<desktop>" in serialized
    assert document["schema_version"] == 1
    assert [item["event"] for item in document["events"][:2]] == ["task_start", "environment"]


def test_trace_ring_keeps_fifty(monkeypatch, tmp_path):
    from reasonix_computer_use import trace

    monkeypatch.setattr(trace, "memory_dir", lambda: tmp_path)
    monkeypatch.setattr(trace, "_read_index", lambda: {})
    for _ in range(55):
        trace.start_trace()
    assert len(trace.list_traces(60)) == 50


def test_trace_size_and_recording_overhead_gate(monkeypatch, tmp_path):
    from reasonix_computer_use import trace

    monkeypatch.setattr(trace, "memory_dir", lambda: tmp_path)
    monkeypatch.setattr(trace, "_read_index", lambda: {})
    trace_id = trace.start_trace()
    elapsed = []
    for index in range(30):
        started = time.perf_counter()
        assert trace.record_event(trace_id, "action", {
            "revision": f"r{index}",
            "actions": [{"type": "type", "text": "private synthetic payload" * 20}],
        })
        elapsed.append((time.perf_counter() - started) * 1000)
    ordered = sorted(elapsed)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    path = trace.trace_dir() / f"{trace_id}.json"
    assert path.stat().st_size <= trace.MAX_TRACE_BYTES
    # CI shared runners are ~3x slower than dev machines (observed
    # 16ms/33ms median/p95 on windows-latest vs <10ms locally); 50ms
    # still catches real regressions (e.g. 100ms+ leaks).
    assert p95 <= 50.0, {"median_ms": statistics.median(elapsed), "p95_ms": p95}


def test_trace_export_requires_existing_trace(monkeypatch, tmp_path):
    from reasonix_computer_use import trace

    monkeypatch.setattr(trace, "memory_dir", lambda: tmp_path / "memory")
    monkeypatch.setattr(trace, "_read_index", lambda: {})
    trace_id = trace.start_trace()
    target = tmp_path / "exports" / "trace.json"
    target.parent.mkdir()
    assert trace.export_trace(trace_id, str(target)) == str(target)
    assert json.loads(target.read_text(encoding="utf-8"))["trace_id"] == trace_id


def test_replay_detects_stale_duplicate_and_unauthorized_fallback():
    from reasonix_computer_use.replay import replay_document

    action = {"revision": "r0", "actions": [{"type": "click_ref", "ref": "e1"}]}
    document = {"trace_id": "SYNTHETIC", "events": [
        {"event": "perception", "data": {"revision": "r1", "source": "uia"}},
        {"event": "action", "data": action},
        {"event": "action", "data": action},
        {"event": "fallback", "data": {"authorized": False}},
    ]}
    result = replay_document(document)
    assert result["ok"] is False
    assert {item["code"] for item in result["violations"]} == {
        "stale_revision", "duplicate_action", "unauthorized_fallback"}


def test_capability_runner_and_matrix_contract():
    import pytest
    pytest.skip("capability_app moved outside plugin dir for install size")
    from reasonix_computer_use.capability_runner import load_matrices, run_quick

    checks = run_quick()
    assert checks and all(item["ok"] for item in checks)
    matrices = load_matrices()
    assert {item["platform"] for item in matrices} == {"windows", "macos", "linux"}


def test_commands_are_reasonix_13_templates():
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads((root / "reasonix-plugin.json").read_text(encoding="utf-8"))
    assert manifest["commands"] == ["commands"]
    assert manifest["agents"] == ["agents"]
    commands = {path.stem: path.read_text(encoding="utf-8") for path in (root / "commands").rglob("*.md")}
    assert set(commands) == {"doctor", "test", "trace", "benchmark", "run", "operator"}
    assert all("description:" in value for value in commands.values())
    assert "$ARGUMENTS" in commands["doctor"]
    assert "/computer-use:agent:operator" in commands["run"]


def test_operator_agent_mapping_and_tool_scope():
    root = Path(__file__).resolve().parent.parent
    text = (root / "agents" / "operator.md").read_text(encoding="utf-8")
    assert "name: operator" in text
    assert "mcp__computer-use__*" in text
    assert "mcp__mimo-mcp__understand_image" in text
    assert "mcp__*__understand_image" not in text
    assert "AskUserQuestion" in text
    assert "Bash" not in text and "Python" not in text


def test_one_hundred_ordinary_prompts_create_no_trace(monkeypatch, tmp_path):
    from hooks import route_guard
    from reasonix_computer_use import trace

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path / "hooks")
    called = []
    monkeypatch.setattr(trace, "start_trace", lambda *a, **k: called.append((a, k)))
    for index in range(100):
        route_guard.handle({"hook_event_name": "UserPromptSubmit", "thread_id": f"normal-{index}",
                            "prompt": f"解释第 {index} 段代码"})
    assert called == []
    assert not (tmp_path / "hooks").exists()


def test_mentioning_run_command_does_not_activate_operator(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    result = route_guard.handle({
        "hook_event_name": "UserPromptSubmit", "thread_id": "mention",
        "prompt": "请解释 /computer-use:run 这个命令的用途",
    })
    assert result is None
    assert route_guard._read_state(route_guard._session_key({"thread_id": "mention"})) == {}


def test_stop_clears_operator_activation(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    payload = {"hook_event_name": "UserPromptSubmit", "thread_id": "stop-test",
               "prompt": "/computer-use:run 打开记事本"}
    route_guard.handle(payload)
    key = route_guard._session_key(payload)
    assert route_guard._read_state(key)["enabled"] is True
    route_guard.handle({"hook_event_name": "Stop", "thread_id": "stop-test"})
    assert route_guard._read_state(key) == {}


def test_active_operator_denies_tools_outside_allowlist(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "thread_id": "scope",
                        "prompt": "/computer-use:run 右键桌面"})
    denied = route_guard.handle({"hook_event_name": "PreToolUse", "thread_id": "scope",
                                 "tool_name": "bash"})
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert route_guard.handle({"hook_event_name": "PreToolUse", "thread_id": "scope",
                               "tool_name": "mcp__computer-use__computer_action"}) is None


def test_active_operator_fails_closed_when_tool_name_is_missing(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path)
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "thread_id": "missing-tool",
                        "prompt": "/computer-use:run 打开记事本"})
    denied = route_guard.handle({"hook_event_name": "PreToolUse", "thread_id": "missing-tool"})
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "tool_name_missing" in denied["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.asyncio
@pytest.mark.skip(reason="legacy: domain_tools removed in 0.8.0-beta.4 refactor; trace contract covered by test_hook_* tests")
async def test_computer_system_trace_contract(monkeypatch, tmp_path):
    from reasonix_computer_use import domain_tools, trace

    monkeypatch.setattr(trace, "memory_dir", lambda: tmp_path)
    monkeypatch.setattr(domain_tools, "trace_dir", lambda: tmp_path / "traces")
    monkeypatch.setattr(domain_tools, "list_traces", lambda limit=20: [])
    status = json.loads(await domain_tools.computer_system({
        "operation": "trace", "params": {"action": "status"}}))
    assert status["schema_version"] == 1
    denied = json.loads(await domain_tools.computer_system({
        "operation": "trace", "params": {"action": "export", "trace_id": "t-a", "destination": "x"}}))
    assert denied["code"] == "confirmation_required"
