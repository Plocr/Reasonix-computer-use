"""Capability test runner for synthetic controls, traces and score reports."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .replay import replay_document, replay_trace, score_documents
from .trace import list_traces, read_trace, sanitize


ROOT = Path(__file__).resolve().parent.parent
MATRIX_ROOT = ROOT / "capability_tests" / "matrix"
APP_PROJECT = ROOT / "capability_app" / "Reasonix.CapabilityApp.csproj"


def _result(name: str, ok: bool, details: Any = None, elapsed_ms: float = 0) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "elapsed_ms": round(elapsed_ms, 2), "details": details}


def _synthetic_trace() -> dict[str, Any]:
    return {
        "schema_version": 1, "trace_id": "SYNTHETIC_TRACE", "created_at": 0, "updated_at": 0,
        "test_mode": True,
        "events": [
            {"event": "perception", "data": {"revision": "r1", "source": "uia", "progress": True}},
            {"event": "action", "data": {"revision": "r1", "actions": [{"type": "click_ref", "ref": "e1"}]}},
            {"event": "perception", "data": {"revision": "r2", "source": "uia", "progress": True}},
            {"event": "task_end", "data": {"status": "completed"}},
        ],
    }


def load_matrices() -> list[dict[str, Any]]:
    result = []
    for path in sorted(MATRIX_ROOT.glob("*.json")) if MATRIX_ROOT.exists() else []:
        value = json.loads(path.read_text(encoding="utf-8"))
        required = {"id", "platform", "language", "dpi", "display_count", "known_folders", "app_types"}
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(f"{path.name} missing: {', '.join(missing)}")
        value["source"] = path.name
        result.append(value)
    return result


def run_quick() -> list[dict[str, Any]]:
    checks = []
    started = time.perf_counter()
    replay = replay_document(_synthetic_trace())
    checks.append(_result("offline-replay", replay["ok"], replay, (time.perf_counter() - started) * 1000))
    secret = "SYNTHETIC_SECRET_TOKEN_123"
    redacted = sanitize({"text": secret, "password": secret, "path": str(Path.home() / "secret.txt")})
    serialized = json.dumps(redacted, ensure_ascii=False)
    checks.append(_result("trace-redaction", secret not in serialized, redacted))
    checks.append(_result("avalonia-project", APP_PROJECT.is_file(), str(APP_PROJECT)))
    return checks


def _validate_app_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    allowed = {"reasonix.capabilityapp.exe", "reasonix.capabilityapp"}
    if path.name.casefold() not in allowed or not path.is_file():
        raise ValueError("online replay only accepts the Reasonix CapabilityApp executable")
    return path


def run_app_smoke(value: str) -> dict[str, Any]:
    path = _validate_app_path(value)
    started = time.perf_counter()
    completed = subprocess.run([str(path), "--automation-smoke"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=20)
    try:
        payload = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        payload = {"stdout": completed.stdout[:500], "stderr": completed.stderr[:500]}
    return _result("avalonia-startup", completed.returncode == 0 and payload.get("ok") is True,
                   payload, (time.perf_counter() - started) * 1000)


async def _online_contract(path: Path) -> dict[str, Any]:
    if os.name != "nt":
        return _result("online-uia-contract", True, {"skipped": "Windows backend only"})
    from .ui_tree import computer_act, observe
    from .windows import list_windows, user32

    process = subprocess.Popen([str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, close_fds=True)
    started = time.perf_counter()
    info = None
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            candidates = [item for item in list_windows()
                          if item.pid == process.pid and "Reasonix Capability Lab" in item.title]
            if candidates:
                info = candidates[0]
                break
            await asyncio.sleep(0.2)
        if info is None:
            return _result("online-uia-contract", False, "capability window not found",
                           (time.perf_counter() - started) * 1000)
        snapshot = observe(hex(info.hwnd), "all", 250)
        by_id = {item.get("id"): item for item in snapshot.get("elements", []) if item.get("id")}
        required = {"InputText", "ApplyButton", "ResultPanel"}
        missing = sorted(required - set(by_id))
        if missing:
            return _result("online-uia-contract", False, {"missing": missing},
                           (time.perf_counter() - started) * 1000)
        typed = json.loads(await computer_act({"ref": by_id["InputText"]["ref"], "action": "set_value",
                                                "value": "SYNTHETIC_HELLO", "verify": True}))
        invoked = json.loads(await computer_act({"ref": by_id["ApplyButton"]["ref"],
                                                  "action": "invoke", "verify": True}))
        await asyncio.sleep(0.2)
        after = observe(hex(info.hwnd), "all", 250)
        result = next((item for item in after.get("elements", [])
                       if item.get("id") == "ResultPanel"), {})
        value = str(result.get("value") or result.get("name") or "")
        ok = typed.get("status") == "ok" and invoked.get("status") == "ok" and "input_applied" in value
        return _result("online-uia-contract", ok, {"typed": typed.get("status"),
                                                    "invoked": invoked.get("status"),
                                                    "result_hash": sanitize({"text": value})["text"]},
                       (time.perf_counter() - started) * 1000)
    finally:
        if info is not None:
            user32.PostMessageW(info.hwnd, 0x0010, 0, 0)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()


def run_full(app: str = "") -> list[dict[str, Any]]:
    checks = run_quick()
    try:
        matrices = load_matrices()
        checks.append(_result("environment-matrix", bool(matrices), {"count": len(matrices)}))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        checks.append(_result("environment-matrix", False, str(exc)))
    if app:
        try:
            checks.append(run_app_smoke(app))
            checks.append(asyncio.run(_online_contract(_validate_app_path(app))))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            checks.append(_result("avalonia-startup", False, str(exc)))
    return checks


def _score_current_traces() -> dict[str, Any]:
    documents = [document for item in list_traces(50)
                 if (document := read_trace(str(item.get("trace_id", ""))))]
    return score_documents(documents)


def _markdown_score(score: dict[str, Any]) -> str:
    return "\n".join((
        "# Reasonix Computer Use Capability Score", "",
        f"- Tasks: {score['tasks']}",
        f"- Completion rate: {score['completion_rate']:.1%}",
        f"- Misoperation rate: {score['misoperation_rate']:.1%}",
        f"- Average tool calls: {score['average_tool_calls']:.2f}",
        f"- Average UIA calls: {score['average_uia_calls']:.2f}",
        f"- Average OCR calls: {score['average_ocr_calls']:.2f}",
        f"- Average visual calls: {score['average_visual_calls']:.2f}",
        f"- Duplicate actions: {score['duplicate_actions']}",
        f"- User interventions: {score['user_interventions']}",
        f"- Unauthorized fallbacks: {score['unauthorized_fallbacks']}",
        f"- Maximum trace size: {score['max_trace_bytes']} bytes",
        f"- Median elapsed: {score['median_elapsed_ms']:.1f} ms",
        f"- P95 elapsed: {score['p95_elapsed_ms']:.1f} ms", ""))


def _write_report(output: str, payload: dict[str, Any], markdown: str = "") -> None:
    if not output:
        return
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown if target.suffix.casefold() == ".md" else
                      json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reasonix-computer-capabilities")
    sub = parser.add_subparsers(dest="command", required=True)
    quick = sub.add_parser("quick")
    quick.add_argument("--output", default="")
    full = sub.add_parser("full")
    full.add_argument("--app", default=os.environ.get("REASONIX_CAPABILITY_APP", ""))
    full.add_argument("--output", default="")
    replay = sub.add_parser("replay")
    replay.add_argument("trace")
    replay.add_argument("--output", default="")
    benchmark = sub.add_parser("benchmark")
    benchmark.add_argument("--output", default="")
    matrix = sub.add_parser("matrix")
    matrix.add_argument("--output", default="")
    args = parser.parse_args(argv)

    if args.command in ("quick", "full"):
        checks = run_quick() if args.command == "quick" else run_full(args.app)
        payload = {"ok": all(item["ok"] for item in checks), "mode": args.command, "checks": checks}
        _write_report(args.output, payload)
    elif args.command == "replay":
        payload = replay_trace(args.trace)
        _write_report(args.output, payload)
    elif args.command == "benchmark":
        payload = _score_current_traces()
        _write_report(args.output, payload, _markdown_score(payload))
    else:
        payload = {"ok": True, "matrices": load_matrices()}
        _write_report(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
