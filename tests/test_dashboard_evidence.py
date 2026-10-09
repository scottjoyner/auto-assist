"""Negative/positive evidence contracts for legacy dashboard projection."""
from assistx.dashboard_evidence import dashboard_evidence


def evidence(nodes=(), router_configured=False, router_ok=False, incidents=()):
    return dashboard_evidence(
        nodes=list(nodes),
        summary={"total_nodes": len(nodes)},
        source_status={"router_configured": router_configured, "router_ok": router_ok},
        health_plan={"incidents": list(incidents)},
    )


def test_empty_snapshot_never_claims_healthy_or_a_measured_zero():
    result = evidence()
    assert result["health_score"] == 0  # legacy integer compatibility
    assert result["health_score_valid"] is False
    assert result["health_score_status"] == "unknown"
    assert all(row["status"] == "unknown" for row in result["component_health"])
    assert result["critical_alerts"] == []


def test_only_fresh_node_reports_contribute_to_health():
    result = evidence([
        {"report_fresh": True, "service_ok": True},
        {"report_fresh": True, "service_ok": False},
        {"report_fresh": False, "service_ok": True},
    ])
    node = result["component_health"][0]
    assert node["score_valid"] is True
    assert node["score"] == 50
    assert node["status"] == "degraded"
    assert node["sample_size"] == 2


def test_unavailable_router_report_never_defaults_to_healthy():
    result = evidence(router_configured=True, router_ok=False)
    router = result["component_health"][1]
    assert router["score"] == 0
    assert router["status"] == "warning"
    assert result["health_score_valid"] is True


def test_single_snapshot_never_invents_positive_or_negative_trend():
    result = evidence([{"report_fresh": True, "service_ok": True}])
    trend = result["trend_data"][0]
    assert trend["current"] == 1
    assert trend["history_available"] is False
    assert trend["trend"] == "insufficient_history"
    assert trend["comparison"] is None
    assert trend["severity_basis"] == "historical_baseline_unavailable"


def test_only_explicit_critical_incident_is_reported():
    result = evidence(incidents=[
        {"id": "critical-1", "severity": "critical", "private": "never echo"},
        {"id": "warning-2", "severity": "warning"},
    ])
    assert len(result["critical_alerts"]) == 1
    assert result["critical_alerts"][0]["incident_id"] == "critical-1"
    assert "private" not in result["critical_alerts"][0]
    assert result["export_url"] == "/api/fleet/dashboard"
