"""Read-only dashboard evidence and restored template inheritance contracts."""
import pytest
from fastapi.testclient import TestClient
import assistx.api as api
from assistx.dashboard_readonly import derive_dashboard_health


def test_no_fresh_reports_are_unknown_not_green_or_critical():
    out = derive_dashboard_health([])
    assert out["health_score_status"] == "unknown"
    assert out["health_score"] == 0
    assert out["component_health"][0]["status"] == "unknown"
    assert out["component_health"][0]["score_known"] is False
    assert out["critical_alerts"] == []
    assert out["trend_data"][0]["current"] is None
    assert out["trend_data"][0]["historical_evidence"] is False
    assert out["trend_data"][0]["trend"] == "insufficient_history"


def test_unverified_legacy_node_does_not_inflate_healthy_score():
    out = derive_dashboard_health([
        {"hostname": "stale", "report_fresh": False, "service_ok": True},
        {"hostname": "fresh-offline", "report_fresh": True, "service_ok": False},
    ])
    item = out["component_health"][0]
    assert out["health_score_status"] == "measured"
    assert out["health_score"] == 0
    assert item["reported_count"] == 1
    assert item["unverified_count"] == 1
    assert item["status"] == "critical"
    assert len(out["critical_alerts"]) == 1
    assert out["trend_data"][0]["historical_evidence"] is False


def test_measured_online_only_counts_explicit_fresh_boolean_observation():
    out = derive_dashboard_health([
        {"report_fresh": True, "service_ok": True},
        {"report_fresh": True, "service_ok": "unknown"},
        {"report_fresh": False, "service_ok": True},
    ])
    assert out["health_score_status"] == "measured"
    assert out["health_score"] == 100
    assert out["component_health"][0]["reported_count"] == 1
    assert out["component_health"][0]["unverified_count"] == 2
    assert out["critical_alerts"] == []


@pytest.fixture()
def client():
    from assistx.api import app, auth
    app.dependency_overrides[auth] = lambda: "synthetic-readonly-operator"
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("path,expected",[
    ("/fleet-dashboard","section-inference-state"),
    ("/fleet-dashboard","component-health-item"),
    ("/operations","ops-shell"),
    ("/control-room","runtime-body"),
])
def test_operator_routes_render_their_own_content_not_fallback(client,path,expected):
    page=client.get(path)
    assert page.status_code==200
    assert expected in page.text
    assert page.text.count("<!doctype html>")==1
    if path != "/control-room":
        assert 'id="runtime-body"' not in page.text
