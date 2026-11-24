"""Regression tests for the issue batch closed in the 0.9.0-preview wrap-up.

  issue #1 — type verification used to abort the rest of the batch
  issue #2 — MCP loop shut down on a blank line / plugin hot-update
  issue #3 — mode="ocr" was ignored, precision cached results won
"""

from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest

from reasonix_computer_use.protocol import ScreenSnapshot, ElementRef

# The tools package exports an async function named ``screen_interactor``
# that shadows the module of the same name, so resolve the module explicitly.
si_mod = importlib.import_module("reasonix_computer_use.tools.screen_interactor")


# ── Fakes ───────────────────────────────────────────────────────────────────


class FakePlatform:
    def __init__(self, title: str = "Calculator"):
        self.calls: list[tuple] = []
        self._title = title

    def get_foreground_window(self):
        return SimpleNamespace(
            id="1", title=self._title, rect=(0, 0, 800, 600),
            process_id=42, process_name="calc.exe",
        )

    def keyboard_type(self, text: str) -> None:
        self.calls.append(("type", text))

    def keyboard_press(self, keys) -> None:
        self.calls.append(("press", list(keys)))

    def keyboard_key_down(self, key) -> None:
        self.calls.append(("down", key))

    def keyboard_key_up(self, key) -> None:
        self.calls.append(("up", key))

    def mouse_click(self, x, y, button="left", count=1) -> None:
        self.calls.append(("click", x, y, button, count))

    def mouse_move(self, x, y) -> None:
        self.calls.append(("move", x, y))

    def mouse_scroll(self, x, y, amount) -> None:
        self.calls.append(("scroll", x, y, amount))

    def mouse_drag(self, x, y, to_x, to_y, duration=0.3) -> None:
        self.calls.append(("drag", x, y, to_x, to_y))

    def screenshot(self):
        from PIL import Image
        return Image.new("RGB", (10, 10))


class FakeRouter:
    """Records every observe() call and returns a canned snapshot."""

    def __init__(self, source: str = "precision", elements=None):
        self.calls: list[dict] = []
        self.source = source
        self.elements = elements if elements is not None else []
        self.vision_available = True

    def observe(self, window_id=None, max_elements=80, force_vision=False,
                prefer_precision=False):
        self.calls.append({
            "window_id": window_id, "max_elements": max_elements,
            "force_vision": force_vision, "prefer_precision": prefer_precision,
        })
        return ScreenSnapshot(
            revision=1, window_id=str(window_id or "foreground"),
            source=self.source, elements=list(self.elements),
        )


def make_interactor(router=None, platform=None) -> si_mod.ScreenInteractor:
    si = si_mod.ScreenInteractor.__new__(si_mod.ScreenInteractor)
    si._router = router or FakeRouter()
    si._platform = platform or FakePlatform()
    from reasonix_computer_use.protocol import CoordinateConverter
    si._converter = CoordinateConverter.from_system_index()
    si._latest_snapshot = None
    si._revision = 0
    si._was_blocked = False
    si._annotated_image = None
    return si


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_replay_guard(monkeypatch):
    """Keep the cross-process replay guard out of the unit tests.

    The guard persists signatures under the real Reasonix memory dir, so
    repeated test runs would be rejected as replays of the previous run.
    """
    import reasonix_computer_use.input_guard as guard

    monkeypatch.setattr(guard, "reserve_text_input", lambda **_kwargs: True)


# ── issue #1: failures never abort the rest of the batch ────────────────────


def test_type_then_enter_both_execute_on_non_text_app():
    """Calculator-style scenario: type + press ENTER must both run."""
    platform = FakePlatform()
    si = make_interactor(platform=platform)

    result = run(si.execute(actions=[
        {"type": "type", "text": "1024*768"},
        {"type": "press", "keys": ["ENTER"]},
    ]))

    assert result["status"] == "ok"
    assert result["actions_executed"] == 2
    assert [r["status"] for r in result["results"]] == ["ok", "ok"]
    assert ("type", "1024*768") in platform.calls
    assert ("press", ["ENTER"]) in platform.calls
    # No OCR literal-match claim is made for injected text anymore.
    assert result["results"][0]["input_verification"] == "unverified"


def test_empty_type_text_does_not_abort_batch():
    platform = FakePlatform()
    si = make_interactor(platform=platform)

    result = run(si.execute(actions=[
        {"type": "type", "text": ""},
        {"type": "press", "keys": ["ENTER"]},
    ]))

    assert result["actions_executed"] == 2
    assert result["results"][0]["status"] == "error"
    assert result["results"][0]["code"] == "empty_text"
    assert result["results"][1]["status"] == "ok"
    assert ("press", ["ENTER"]) in platform.calls
    assert any("empty_text" in w or "type" in w for w in result["warnings"])


def test_raising_action_keeps_remaining_actions_running(monkeypatch):
    platform = FakePlatform()
    si = make_interactor(platform=platform)

    def boom(*_a, **_k):
        raise RuntimeError("injection failed")

    monkeypatch.setattr(si._platform, "mouse_click", boom)

    result = run(si.execute(actions=[
        {"type": "click", "fallback": {"x": 10, "y": 10, "space": "CLAUDE_1024"}},
        {"type": "press", "keys": ["ENTER"]},
    ]))

    assert result["actions_executed"] == 2
    assert result["results"][0]["status"] == "error"
    assert "injection failed" in result["results"][0]["error"]
    assert result["results"][1]["status"] == "ok"
    assert ("press", ["ENTER"]) in platform.calls


# ── issue #3: explicit mode is honoured ────────────────────────────────────


def test_computer_state_mode_ocr_forces_vision(monkeypatch):
    """The legacy alias must forward mode="ocr" to the router (issue #3)."""
    from reasonix_computer_use.tools import TOOLS, _get_si

    router = FakeRouter(source="vision")
    si = make_interactor(router=router)
    monkeypatch.setattr(si_mod.ScreenInteractor, "__init__",
                        lambda self: self.__dict__.update(si.__dict__))
    monkeypatch.setattr("reasonix_computer_use.tools._SI_INSTANCE", si)

    handler = TOOLS["computer_state"]["handler"]
    payload = json.loads(run(handler({"window_id": "7", "mode": "ocr"})))

    assert router.calls[-1]["force_vision"] is True
    assert payload["requested_mode"] == "ocr"
    assert payload["source"] == "vision"
    assert payload["vision_used"] is True


def test_computer_state_mode_precision_overrides_blacklist():
    si = make_interactor(router=FakeRouter())
    run(si.observe(window_id="7", mode="uia"))
    assert si._router.calls[-1]["force_vision"] is False
    assert si._router.calls[-1]["prefer_precision"] is True


def test_computer_state_mode_is_reported_and_vision_available():
    si = make_interactor(router=FakeRouter(source="vision"))
    payload = run(si.observe(window_id="7", mode="ocr"))
    assert payload["vision_available"] is True
    assert payload["vision_used"] is True
    # Unknown mode strings fall back to the legacy boolean instead of failing.
    payload2 = run(si.observe(window_id="7", mode="bogus", force_vision=True))
    assert payload2["requested_mode"] == "ocr"
    assert si._router.calls[-1]["force_vision"] is True


def test_router_force_vision_skips_precision_even_with_elements():
    """A precision result with elements must not satisfy force_vision."""
    from reasonix_computer_use.perception import PerceptionRouter

    router = PerceptionRouter()
    el = ElementRef(id="e1", bbox=(0, 0, 10, 10), text="hello")

    class Precision:
        available = True

        def __init__(self):
            self.hits = 0

        def observe(self, window_id, max_elements):
            self.hits += 1
            return ScreenSnapshot(1, "w", "precision", elements=[el])

    class Vision:
        available = True

        def observe(self, window_id, max_elements):
            return ScreenSnapshot(1, "w", "vision", elements=[el])

    precision = Precision()
    router._precision = precision
    router._precision_macos = Precision()
    router._precision_linux = Precision()
    router._vision_easy = Vision()

    import sys
    if sys.platform.startswith("win"):
        snap = router.observe("w", 10, force_vision=True)
        assert snap.source == "vision"
        assert precision.hits == 0


# ── issue #2: save_as drives the dialog instead of doing nothing ────────────


def test_save_as_injects_path_into_detected_dialog():
    platform = FakePlatform(title="另存为 - Typora")
    si = make_interactor(platform=platform)

    result = run(si.execute(actions=[
        {"type": "save_as", "path": "D:\\out\\note.md"},
    ]))

    step = result["results"][0]
    assert step["status"] == "ok", step
    assert step["path"] == "D:\\out\\note.md"
    assert ("press", ["ctrl", "shift", "s"]) in platform.calls
    assert ("type", "D:\\out\\note.md") in platform.calls
    assert ("press", ["enter"]) in platform.calls
    assert result["warnings"]


def test_save_as_refuses_to_type_when_no_dialog():
    platform = FakePlatform(title="Typora - note.md")
    si = make_interactor(platform=platform)

    step = run(si.execute(actions=[
        {"type": "save_as", "path": "D:\\out\\note.md"},
    ]))["results"][0]

    assert step["status"] == "error"
    assert step["code"] == "save_dialog_not_detected"
    assert not any(call[0] == "type" for call in platform.calls), \
        "a missing dialog must never leak the path into the document body"


def test_save_as_requires_path():
    si = make_interactor()
    step = run(si.execute(actions=[{"type": "save_as"}]))["results"][0]
    assert step["status"] == "error"
    assert step["code"] == "missing_path"


# ── issue #2: the MCP loop survives keepalive lines and hot updates ────────


def test_read_request_skips_blank_lines(monkeypatch):
    import io
    import reasonix_computer_use.mcp_server as srv

    stream = io.StringIO('\n\n{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n')
    monkeypatch.setattr(srv.sys, "stdin", stream)

    request = run(srv.read_request())
    assert request == {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def test_main_loop_survives_bad_json_and_source_change(monkeypatch, capsys):
    import io
    import reasonix_computer_use.mcp_server as srv

    lines = io.StringIO(
        "not json\n"
        '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
        '{"jsonrpc":"2.0","id":2,"method":"initialize"}\n'
    )
    monkeypatch.setattr(srv.sys, "stdin", lines)
    out = io.StringIO()
    monkeypatch.setattr(srv.sys, "stdout", out)

    # Force the "plugin updated on disk" branch on the second call.
    monkeypatch.setattr(srv, "_source_signature", lambda paths=None: ("changed",))

    run(srv.main())

    written = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert written and written[0]["error"]["code"] == -32002
    assert written[-1]["error"]["code"] == -32002
