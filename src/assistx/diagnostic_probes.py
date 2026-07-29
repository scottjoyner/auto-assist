from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


def execute_diagnostic_probes(
    diagnosis: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    service_probe: Callable[[dict[str, Any]], dict[str, Any]],
    canary_probe: Callable[[dict[str, Any], str | None], dict[str, Any]] | None = None,
    deadline_seconds: float = 20.0,
) -> dict[str, Any]:
    """Execute only the diagnosis-declared probe allowlist within a deadline."""
    started = time.monotonic()
    node = next(
        (
            row for row in snapshot.get("nodes") or []
            if str(row.get("hostname") or row.get("id")) == str(diagnosis.get("node_id"))
        ),
        {},
    )
    results = []
    for probe in diagnosis.get("bounded_probes") or []:
        if time.monotonic() - started >= deadline_seconds:
            results.append({"probe": probe, "ok": False, "reason": "diagnostic_deadline_exceeded"})
            continue
        if probe in {"service_probe", "network_probe"}:
            result = service_probe(node)
        elif probe in {"inventory_probe", "model_inventory"}:
            result = {
                "ok": bool(node.get("report_fresh")),
                "loaded_models": list(node.get("loaded_models") or []),
                "available_models": list(node.get("available_models") or []),
                "reason": "" if node.get("report_fresh") else "inventory_report_stale",
            }
        elif probe == "agent_heartbeat":
            age = node.get("last_seen_ago_sec")
            result = {
                "ok": age is not None and float(age) <= 60,
                "last_seen_ago_sec": age,
                "reason": "" if age is not None and float(age) <= 60 else "agent_heartbeat_stale",
            }
        elif probe == "capacity_probe":
            capacity = max(1, int(node.get("max_concurrent") or 1))
            inflight = int(node.get("inflight_tasks") or 0)
            result = {
                "ok": inflight < capacity,
                "inflight": inflight,
                "capacity": capacity,
                "reason": "" if inflight < capacity else "node_at_capacity",
            }
        elif probe == "recent_failures":
            result = {
                "ok": diagnosis.get("incident_type") != "high_failure_rate",
                "incident_evidence": diagnosis.get("evidence") or [],
                "reason": "failure_rate_incident_active" if diagnosis.get("incident_type") == "high_failure_rate" else "",
            }
        elif probe == "canary_inference" and canary_probe:
            result = canary_probe(node, diagnosis.get("model_id"))
        else:
            result = {"ok": False, "reason": "probe_adapter_unavailable"}
        results.append({"probe": probe, **result})

    completed = [row for row in results if row.get("reason") != "probe_adapter_unavailable"]
    successes = sum(1 for row in completed if row.get("ok"))
    evidence_coverage = len(completed) / max(len(results), 1)
    confidence = min(
        0.98,
        max(
            0.15,
            0.25 + evidence_coverage * 0.45 + (successes / max(len(completed), 1)) * 0.2,
        ),
    )
    return {
        **diagnosis,
        "probe_results": results,
        "probe_summary": {
            "requested": len(results),
            "completed": len(completed),
            "successful": successes,
            "deadline_seconds": deadline_seconds,
        },
        "confidence": round(confidence, 2),
        "evidence_status": "measured",
        "mutated": False,
    }
