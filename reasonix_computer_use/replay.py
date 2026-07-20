"""Offline trace replay and capability scoring. Never performs GUI actions."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from .trace import read_trace


STRATEGIES = {"memory": 0, "visual": 1}


def replay_document(document: dict[str, Any]) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    latest_revision = ""
    signatures: set[tuple[str, str]] = set()
    blocked = False
    last_strategy = -1
    for index, item in enumerate(document.get("events", [])):
        event = item.get("event")
        data = item.get("data", {})
        if event in ("window_revision", "perception") and isinstance(data.get("revision"), str):
            latest_revision = data["revision"]
        if event == "perception":
            strategy = STRATEGIES.get(str(data.get("source", "")))
            progress = bool(data.get("progress"))
            if strategy is not None and strategy < last_strategy and not progress:
                violations.append({"index": index, "code": "strategy_regressed"})
            if strategy is not None:
                last_strategy = strategy if not progress else min(strategy, 1)
            blocked = blocked or bool(data.get("blocked"))
        if event == "action":
            revision = str(data.get("revision", ""))
            signature = json.dumps(data.get("actions", []), ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"))
            if blocked:
                violations.append({"index": index, "code": "action_after_blocked"})
            if latest_revision and revision and revision != latest_revision:
                violations.append({"index": index, "code": "stale_revision"})
            key = (revision, signature)
            if key in signatures:
                violations.append({"index": index, "code": "duplicate_action"})
            signatures.add(key)
        if event == "fallback" and not data.get("authorized"):
            violations.append({"index": index, "code": "unauthorized_fallback"})
    return {"ok": not violations, "trace_id": document.get("trace_id"),
            "events": len(document.get("events", [])), "violations": violations}


def replay_trace(trace_id_or_path: str) -> dict[str, Any]:
    path = Path(trace_id_or_path)
    if path.is_file():
        document = json.loads(path.read_text(encoding="utf-8"))
    else:
        document = read_trace(trace_id_or_path)
    if not document:
        raise FileNotFoundError(trace_id_or_path)
    return replay_document(document)


def score_documents(documents: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = []
    for document in documents:
        events = document.get("events", [])
        calls = [item for item in events if item.get("event") in ("action", "perception", "window_revision")]
        visual = sum(1 for item in events if item.get("event") == "perception"
                     and item.get("data", {}).get("source") == "visual")
        action_events = [item for item in events if item.get("event") == "action"]
        misoperations = sum(1 for item in action_events
                            if item.get("data", {}).get("status") not in (None, "ok")
                            or item.get("data", {}).get("verification", {}).get("ok") is False)
        duplicate = len([item for item in replay_document(document)["violations"]
                         if item["code"] == "duplicate_action"])
        unauthorized = sum(1 for item in events if item.get("event") == "fallback"
                           and not item.get("data", {}).get("authorized"))
        interventions = sum(1 for item in events if item.get("event") == "perception"
                            and item.get("data", {}).get("blocked"))
        terminal = next((item for item in reversed(events) if item.get("event") == "task_end"), {})
        completed = terminal.get("data", {}).get("status") == "completed"
        elapsed = max(0.0, float(document.get("updated_at", 0)) - float(document.get("created_at", 0)))
        tasks.append({"completed": completed, "calls": len(calls), "visual": visual,
                      "actions": len(action_events),
                      "misoperations": misoperations, "duplicates": duplicate,
                      "unauthorized": unauthorized, "interventions": interventions,
                      "trace_bytes": len(json.dumps(document, ensure_ascii=False,
                                                     separators=(",", ":")).encode("utf-8")),
                      "elapsed_ms": elapsed * 1000})
    elapsed = [item["elapsed_ms"] for item in tasks]
    ordered = sorted(elapsed)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 0
    return {
        "tasks": len(tasks),
        "completion_rate": sum(item["completed"] for item in tasks) / len(tasks) if tasks else 0,
        "misoperation_rate": (sum(item["misoperations"] for item in tasks) /
                              sum(item["actions"] for item in tasks)) if sum(item["actions"] for item in tasks) else 0,
        "average_tool_calls": statistics.mean(item["calls"] for item in tasks) if tasks else 0,
        "average_visual_calls": statistics.mean(item["visual"] for item in tasks) if tasks else 0,
        "duplicate_actions": sum(item["duplicates"] for item in tasks),
        "user_interventions": sum(item["interventions"] for item in tasks),
        "unauthorized_fallbacks": sum(item["unauthorized"] for item in tasks),
        "average_trace_bytes": statistics.mean(item["trace_bytes"] for item in tasks) if tasks else 0,
        "max_trace_bytes": max((item["trace_bytes"] for item in tasks), default=0),
        "median_elapsed_ms": statistics.median(elapsed) if elapsed else 0,
        "p95_elapsed_ms": p95,
    }
