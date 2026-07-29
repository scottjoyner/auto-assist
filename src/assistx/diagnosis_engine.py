from __future__ import annotations

import hashlib
import json
from typing import Any

PLAYBOOKS = {
    "node_offline": ("restore_service", "critical", ["network_probe", "service_probe", "inventory_probe"]),
    "stale_report": ("refresh_agent", "low", ["agent_heartbeat", "inventory_probe"]),
    "model_unavailable": ("reload_model", "medium", ["service_probe", "model_inventory"]),
    "high_failure_rate": ("drain_and_test", "medium", ["recent_failures", "canary_inference"]),
    "high_latency": ("drain_and_benchmark", "low", ["capacity_probe", "canary_inference"]),
}


def diagnose_incident(incident: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Produce an explainable, bounded diagnosis without mutating the fleet."""
    incident_type = str(incident.get("incident_type") or "unknown")
    node_id = str(incident.get("node_id") or "")
    node = next(
        (row for row in snapshot.get("nodes") or [] if str(row.get("hostname") or row.get("id")) == node_id),
        {},
    )
    action, risk, probes = PLAYBOOKS.get(
        incident_type,
        ("collect_evidence", "low", ["service_probe", "recent_failures"]),
    )
    evidence = [
        {"signal": "service_online", "value": bool(node.get("service_ok") or node.get("online")), "source": "live_probe"},
        {"signal": "report_fresh", "value": bool(node.get("report_fresh")), "source": "node_report"},
        {"signal": "inflight_tasks", "value": int(node.get("inflight_tasks") or 0), "source": "executor"},
        {"signal": "loaded_models", "value": list(node.get("loaded_models") or []), "source": "node_report"},
        {"signal": "incident_detail", "value": incident.get("detail"), "source": "health_plan"},
    ]
    missing = [row["signal"] for row in evidence if row["value"] in (None, [], "")]
    confidence = max(0.2, min(0.95, 0.82 - len(missing) * 0.1))
    identity = json.dumps(
        {
            "incident_key": incident.get("incident_key"),
            "type": incident_type,
            "node": node_id,
            "evidence": evidence,
        },
        sort_keys=True,
        default=str,
    )
    return {
        "diagnosis_id": f"diag-{hashlib.sha256(identity.encode()).hexdigest()[:20]}",
        "incident_key": incident.get("incident_key"),
        "node_id": node_id,
        "model_id": incident.get("model_id"),
        "incident_type": incident_type,
        "severity": incident.get("severity") or "warning",
        "hypothesis": _hypothesis(incident_type, node),
        "confidence": round(confidence, 2),
        "evidence": evidence,
        "missing_evidence": missing,
        "bounded_probes": probes,
        "recommended_recovery": {
            "action": action,
            "parameters": {
                "model_id": incident.get("model_id"),
                "service_alias": "inference" if action == "restore_service" else None,
            },
            "risk": risk,
            "requires_approval": action not in {"collect_evidence"},
            "verify_after": ["service_online", "report_fresh"],
            "rollback": "restore_previous_control_state",
        },
        "mutated": False,
    }


def _hypothesis(incident_type: str, node: dict[str, Any]) -> str:
    if incident_type == "node_offline":
        return "The inference service or its network path is unavailable."
    if incident_type == "stale_report":
        return "The node agent stopped publishing fresh inventory."
    if incident_type == "model_unavailable":
        return "The expected model is absent from the node's live loadout."
    if incident_type == "high_failure_rate":
        return "Recent requests indicate a model, runtime, or node-specific regression."
    if incident_type == "high_latency":
        return "Observed latency exceeds the useful capacity of the current placement."
    if not node:
        return "The incident cannot yet be correlated with a live node report."
    return "Available evidence is insufficient for a specific root-cause claim."
