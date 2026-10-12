"""Read-only dashboard compatibility projection from one observed snapshot.

Never manufacture historical deltas or turn unknown fleet telemetry into health.
The old int score is retained for API compatibility, with a separate validity
flag to prevent consumers from presenting "0" as a measured system outage.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def dashboard_evidence(
    *,
    nodes: list[dict[str, Any]],
    summary: Mapping[str, Any],
    source_status: Mapping[str, Any],
    health_plan: Mapping[str, Any],
) -> dict[str, Any]:
    fresh_nodes = [node for node in nodes if node.get("report_fresh") is True]
    healthy_fresh = [node for node in fresh_nodes if node.get("service_ok") is True]
    components: list[dict[str, Any]] = []

    if not fresh_nodes:
        components.append({
            "name": "Node health observations",
            "status": "unknown",
            "score": 0,
            "score_valid": False,
            "issues": ["No fresh, reported node health available"],
            "source": "fleet.nodes.report_fresh",
        })
    else:
        score = round(100 * len(healthy_fresh) / len(fresh_nodes))
        components.append({
            "name": "Node health observations",
            "status": "healthy" if len(healthy_fresh) == len(fresh_nodes) else "degraded",
            "score": score,
            "score_valid": True,
            "issues": [] if score == 100 else [
                f"{len(fresh_nodes) - len(healthy_fresh)} of {len(fresh_nodes)} fresh reports not healthy"
            ],
            "sample_size": len(fresh_nodes),
            "source": "fleet.nodes.report_fresh+service_ok",
        })

    if source_status.get("router_configured") is True:
        ok = source_status.get("router_ok") is True
        components.append({
            "name": "Router snapshot",
            "status": "healthy" if ok else "warning",
            "score": 100 if ok else 0,
            "score_valid": True,
            "issues": [] if ok else ["Router report unavailable in this snapshot"],
            "source": "fleet.source_status.router_ok",
        })
    else:
        components.append({
            "name": "Router snapshot",
            "status": "unknown",
            "score": 0,
            "score_valid": False,
            "issues": ["Router not configured or not evidenced"],
            "source": "fleet.source_status",
        })

    measured = [row["score"] for row in components if row["score_valid"]]
    # Legacy score is integer; validity distinguishes a missing observation
    # from a genuine zero score. UI must display an em dash if invalid.
    health_score = round(sum(measured) / len(measured)) if measured else 0

    incidents = health_plan.get("incidents")
    alerts = []
    if isinstance(incidents, list):
        for incident in incidents:
            if not isinstance(incident, Mapping):
                continue
            if str(incident.get("severity") or "").lower() != "critical":
                continue
            alerts.append({
                "source": "fleet.health_plan.incidents",
                "incident_id": str(incident.get("id") or incident.get("incident_id") or ""),
                "severity": "critical",
                "message": "Critical incident reported by fleet health plan",
            })

    total_nodes = summary.get("total_nodes")
    if not isinstance(total_nodes, int):
        total_nodes = len(nodes)
    # One snapshot cannot establish an increasing/stable/decreasing trend.
    trends = [{
        "metric": "Nodes in current inventory",
        "current": total_nodes,
        "trend": "insufficient_history",
        "severity": "warning",
        "severity_basis": "historical_baseline_unavailable",
        "comparison": None,
        "history_available": False,
        "source": "fleet.summary.total_nodes",
    }]

    return {
        "component_health": components,
        "health_score": health_score,
        "health_score_valid": bool(measured),
        "health_score_status": "measured" if measured else "unknown",
        "trend_data": trends,
        "trend_history_available": False,
        "critical_alerts": alerts,
        # Existing authenticated GET endpoint serves an exportable JSON snapshot.
        "export_available": True,
        "export_url": "/api/fleet/dashboard",
    }
