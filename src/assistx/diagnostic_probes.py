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
    requested = list(diagnosis.get("bounded_probes") or [])
    raw_target = diagnosis.get("node_id")
    target_id = "" if raw_target is None else str(raw_target).strip()
    node = None
    if target_id:
        for row in snapshot.get("nodes") or []:
            candidate = row.get("hostname") or row.get("id")
            if candidate is not None and str(candidate).strip() == target_id:
                node = row
                break
    if node is None:
        # No identified target in the snapshot. Running probes against an empty
        # stub would manufacture "successes" (e.g. capacity from its defaults)
        # and let a diagnosis be persisted as measured evidence. Fail closed.
        results = [
            {"probe": probe, "ok": False, "reason": "target_node_missing"}
            for probe in requested
        ]
        return {
            **diagnosis,
            "probe_results": results,
            "probe_summary": {
                "requested": len(results),
                "completed": 0,
                "successful": 0,
                "deadline_seconds": deadline_seconds,
            },
            "confidence": 0.0,
            "evidence_status": "target_node_missing",
            "mutated": False,
        }
    results = []
    for probe in requested:
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

    # A skipped adapter or a deadline skip never executed a probe; neither may
    # inflate evidence coverage or turn the verdict into "measured".
    incomplete = {"probe_adapter_unavailable", "diagnostic_deadline_exceeded"}
    completed = [row for row in results if row.get("reason") not in incomplete]
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
        "evidence_status": "measured" if completed else "unavailable",
        "mutated": False,
    }
