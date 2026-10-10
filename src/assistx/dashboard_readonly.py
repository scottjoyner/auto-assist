"""Read-only fleet dashboard summaries; no invented history or health evidence.

A snapshot is not a trend. Unknown reporting state must not be shown as
healthy, nor silently converted into an all-green percentage. All fields are
advisory display data and convey no admission or mutation authority.
"""
from __future__ import annotations
from typing import Any


def derive_dashboard_health(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    fresh = [
        n for n in nodes
        if isinstance(n, dict) and n.get("report_fresh") is True
        and type(n.get("service_ok")) is bool
    ]
    online = sum(n["service_ok"] for n in fresh)
    if fresh:
        score = round(100 * online / len(fresh))
        state = "healthy" if online == len(fresh) else (
            "critical" if online == 0 else "degraded"
        )
        issues = [] if online == len(fresh) else [
            f"{len(fresh) - online} fresh reported node(s) unavailable"
        ]
    else:
        # No fresh evidence is *unknown*, not a measured zero-percent outage.
        score, state = 0, "unknown"
        issues = ["No fresh node health reports; score unavailable"]
    component = {
        "name": "Fresh fleet node connectivity",
        "category": "fleet",
        "status": state,
        "score": score,
        "score_known": bool(fresh),
        "issues": issues,
        "reported_count": len(fresh),
        "unverified_count": len(nodes) - len(fresh),
    }
    # Single snapshot alone cannot establish increasing/decreasing trends.
    trend = {
        "metric": "Fresh reported nodes online",
        "current": online if fresh else None,
        "trend": "insufficient_history",
        "severity": "warning" if not fresh else ("critical" if online == 0 else "stable"),
        "historical_evidence": False,
    }
    return {
        "component_health": [component],
        "trend_data": [trend],
        "health_score": score,
        "health_score_status": "measured" if fresh else "unknown",
        "critical_alerts": (
            [{"name": component["name"], "detail": issues[0]}]
            if fresh and online == 0 else []
        ),
        # An authenticated snapshot can be exported locally from the UI.
        "export_available": True,
    }
