from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import pytest


def test_hook_modes_and_fallback_threshold(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path / "hooks")
    monkeypatch.setattr("reasonix_computer_use.trace.memory_dir", lambda: tmp_path / "memory")

    strict = route_guard.classify_prompt("必须使用计算器逐个点击完成计算，不要脚本")
    preferred = route_guard.classify_prompt("打开计算器完成计算")
    result_only = route_guard.classify_prompt("使用Python脚本计算结果")
    music = route_guard.classify_prompt("打开QQ音乐，放我喜欢的歌听")
    assert strict["mode"] == "strict_gui"
    assert preferred["mode"] == "gui_preferred"
    assert result_only["mode"] == "result_only"
    assert music["mode"] == "gui_preferred"

    session = "preferred"
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": session,
                        "prompt": "打开计算器完成计算"})
    for index in range(3):
        route_guard.handle({"hook_event_name": "PostToolUse", "session_id": session,
                            "tool_name": "computer_action",
                            "tool_result": {"status": "error" if index < 2 else "ok"}})
    allowed = route_guard.handle({"hook_event_name": "PreToolUse", "session_id": session,
                                  "tool_name": "bash"})
    assert "permissionDecision" not in allowed["hookSpecificOutput"]
    state = route_guard._read_state(route_guard._session_key({"session_id": session}))
    assert state["fallback_authorized"] is True


def test_hook_blocked_authorizes_preferred_but_not_strict(monkeypatch, tmp_path):
    from hooks import route_guard

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path / "hooks")
    monkeypatch.setattr("reasonix_computer_use.trace.memory_dir", lambda: tmp_path / "memory")
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": "p",
                        "prompt": "打开WPS编辑表格"})
    stopped = route_guard.handle({"hook_event_name": "PostToolUse", "session_id": "p",
                                  "tool_name": "computer_state",
                                  "tool_result": {"status": "ok", "blocked": True}})
    assert route_guard._read_state(route_guard._session_key({"session_id": "p"}))["fallback_authorized"] is True
    assert "停止重复" in stopped["hookSpecificOutput"]["additionalContext"]
    denied_repeat = route_guard.handle({"hook_event_name": "PreToolUse", "session_id": "p",
                                        "tool_name": "computer_app"})
    assert denied_repeat["hookSpecificOutput"]["permissionDecision"] == "deny"

    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s",
                        "prompt": "必须使用WPS逐个点击编辑表格，不要脚本"})
    route_guard.handle({"hook_event_name": "PostToolUse", "session_id": "s",
                        "tool_name": "computer_state", "tool_result": {"status": "ok", "blocked": True}})
    denied = route_guard.handle({"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "bash"})
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_trace_captures_failed_tool_and_task_end(monkeypatch, tmp_path):
    from hooks import route_guard
    from reasonix_computer_use import trace

    monkeypatch.setattr(route_guard, "_state_root", lambda: tmp_path / "hooks")
    monkeypatch.setattr(trace, "memory_dir", lambda: tmp_path / "memory")
    monkeypatch.setattr(trace, "read_index", lambda: {})
    session = "trace-task"
    route_guard.handle({"hook_event_name": "UserPromptSubmit", "session_id": session,
                        "prompt": "打开音乐应用播放歌曲"})
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
    monkeypatch.setattr(trace, "read_index", lambda: {"known_folders": {
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
    monkeypatch.setattr(trace, "read_index", lambda: {})
    for _ in range(55):
        trace.start_trace()
    assert len(trace.list_traces(60)) == 50


def test_trace_size_and_recording_overhead_gate(monkeypatch, tmp_path):
    from reasonix_computer_use import trace

    monkeypatch.setattr(trace, "memory_dir", lambda: tmp_path)
    monkeypatch.setattr(trace, "read_index", lambda: {})
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
    assert p95 <= 10.0, {"median_ms": statistics.median(elapsed), "p95_ms": p95}


def test_trace_export_requires_existing_trace(monkeypatch, tmp_path):
    from reasonix_computer_use import trace

    monkeypatch.setattr(trace, "memory_dir", lambda: tmp_path / "memory")
    monkeypatch.setattr(trace, "read_index", lambda: {})
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
    from reasonix_computer_use.capability_runner import load_matrices, run_quick

    checks = run_quick()
    assert checks and all(item["ok"] for item in checks)
    matrices = load_matrices()
    assert {item["platform"] for item in matrices} == {"windows", "macos", "linux"}


def test_commands_are_reasonix_13_templates():
    root = Path(__file__).resolve().parent.parent
    manifest = json.loads((root / "reasonix-plugin.json").read_text(encoding="utf-8"))
    assert manifest["commands"] == ["commands"]
    commands = {path.stem: path.read_text(encoding="utf-8") for path in (root / "commands").glob("*.md")}
    assert set(commands) == {"doctor", "test", "trace", "benchmark"}
    assert all("description:" in value for value in commands.values())
    assert "$ARGUMENTS" in commands["doctor"]


@pytest.mark.asyncio
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
