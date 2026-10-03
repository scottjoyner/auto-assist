"""Tests for the enhanced dashboard functionality.

Tests for the new dashboard features including:
- Component health metrics and scoring
- Trend analysis and predictive indicators
- Export functionality
- Quick actions
- Health score calculation
- Critical alerts
"""
import pytest
from fastapi.testclient import TestClient

import assistx.api as api
from assistx.api import app
from assistx.llm import client as llm_client


class _FakeExecutor:
    _nodes = []
    _node_inflight = {}
    _node_latency = {}

    def _refresh_nodes(self):
        pass

    @property
    def _stop(self):
        class S:
            def is_set(self):
                return False
        return S()


class _FakeRouting:
    _model_to_node = {}
    _routing = {}
    _node_specs = {}
    _state = {}
    _loadout = {"routing": {}}

    def get_model_perf(self, node, model):
        return None

    def update(self, projection):
        pass


class _FakeNeo:
    def _session(self):
        class Ctx:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def run(self, query, params=None):
                return _FakeResult()
            def consume(self):
                pass
        return Ctx()

    def close(self):
        pass


class _FakeResult:
    def data(self):
        return []


class _FakeSelfHealing:
    def status(self):
        return {"automatic_quarantine": False}


class _FakeBenchCtrl:
    def status(self):
        return {"enabled": False}


class _FakeRecovery:
    def status(self):
        return {"execution_enabled": False}


def _clear():
    llm_client._inference_sessions.clear()
    llm_client._inference_session_id = 0


@pytest.fixture(autouse=True)
def _isolate():
    _clear()
    yield
    _clear()


@pytest.fixture()
def client(monkeypatch):
    from assistx.api import auth
    app.dependency_overrides[auth] = lambda: "test-user"
    monkeypatch.setattr(api, "_get_fleet_executor", lambda: _FakeExecutor())
    monkeypatch.setattr(api, "_get_fleet_routing", lambda: _FakeRouting())
    monkeypatch.setattr(api, "_auto_router_base_url", lambda: "")
    monkeypatch.setattr(api, "_neo", lambda: _FakeNeo())
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def test_dashboard_includes_component_health(client, monkeypatch):
    # Stub out the pieces that would need real data
    monkeypatch.setattr(api, "_capacity_task_rows", lambda *a, **kw: [])
    monkeypatch.setattr(api, "build_capacity_forecast", lambda *a, **kw: {})
    monkeypatch.setattr(api, "build_allocation_plan", lambda *a, **kw: {})
    monkeypatch.setattr(api, "diagnose_incident", lambda *a, **kw: {})
    monkeypatch.setattr(api, "_self_healing", _FakeSelfHealing())
    monkeypatch.setattr(api, "_benchmark_controller", _FakeBenchCtrl())
    monkeypatch.setattr(api, "_recovery_control", _FakeRecovery())

    r = client.get("/api/fleet/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert "component_health" in body
    comp_health = body["component_health"]
    assert isinstance(comp_health, list)
    assert len(comp_health) > 0
    component = comp_health[0]
    assert "name" in component
    assert "status" in component
    assert "score" in component
    assert "issues" in component


def test_dashboard_includes_trend_data(client, monkeypatch):
    monkeypatch.setattr(api, "_capacity_task_rows", lambda *a, **kw: [])
    monkeypatch.setattr(api, "build_capacity_forecast", lambda *a, **kw: {})
    monkeypatch.setattr(api, "build_allocation_plan", lambda *a, **kw: {})
    monkeypatch.setattr(api, "diagnose_incident", lambda *a, **kw: {})
    monkeypatch.setattr(api, "_self_healing", _FakeSelfHealing())
    monkeypatch.setattr(api, "_benchmark_controller", _FakeBenchCtrl())
    monkeypatch.setattr(api, "_recovery_control", _FakeRecovery())

    r = client.get("/api/fleet/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert "trend_data" in body
    trends = body["trend_data"]
    assert isinstance(trends, list)
    # Should have at least one trend
    assert len(trends) > 0
    trend = trends[0]
    assert "metric" in trend
    assert "current" in trend
    assert "trend" in trend
    assert "severity" in trend


def test_dashboard_includes_export_functionality(client, monkeypatch):
    monkeypatch.setattr(api, "_capacity_task_rows", lambda *a, **kw: [])
    monkeypatch.setattr(api, "build_capacity_forecast", lambda *a, **kw: {})
    monkeypatch.setattr(api, "build_allocation_plan", lambda *a, **kw: {})
    monkeypatch.setattr(api, "diagnose_incident", lambda *a, **kw: {})
    monkeypatch.setattr(api, "_self_healing", _FakeSelfHealing())
    monkeypatch.setattr(api, "_benchmark_controller", _FakeBenchCtrl())
    monkeypatch.setattr(api, "_recovery_control", _FakeRecovery())

    r = client.get("/api/fleet/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert "export_available" in body
    assert body["export_available"] == True


def test_dashboard_includes_health_score(client, monkeypatch):
    monkeypatch.setattr(api, "_capacity_task_rows", lambda *a, **kw: [])
    monkeypatch.setattr(api, "build_capacity_forecast", lambda *a, **kw: {})
    monkeypatch.setattr(api, "build_allocation_plan", lambda *a, **kw: {})
    monkeypatch.setattr(api, "diagnose_incident", lambda *a, **kw: {})
    monkeypatch.setattr(api, "_self_healing", _FakeSelfHealing())
    monkeypatch.setattr(api, "_benchmark_controller", _FakeBenchCtrl())
    monkeypatch.setattr(api, "_recovery_control", _FakeRecovery())

    r = client.get("/api/fleet/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert "health_score" in body
    assert isinstance(body["health_score"], int)
    assert 0 <= body["health_score"] <= 100


def test_dashboard_includes_critical_alerts(client, monkeypatch):
    monkeypatch.setattr(api, "_capacity_task_rows", lambda *a, **kw: [])
    monkeypatch.setattr(api, "build_capacity_forecast", lambda *a, **kw: {})
    monkeypatch.setattr(api, "build_allocation_plan", lambda *a, **kw: {})
    monkeypatch.setattr(api, "diagnose_incident", lambda *a, **kw: {})
    monkeypatch.setattr(api, "_self_healing", _FakeSelfHealing())
    monkeypatch.setattr(api, "_benchmark_controller", _FakeBenchCtrl())
    monkeypatch.setattr(api, "_recovery_control", _FakeRecovery())

    r = client.get("/api/fleet/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert "critical_alerts" in body
    alerts = body["critical_alerts"]
    assert isinstance(alerts, list)
    # Should be empty initially
    assert len(alerts) == 0


def test_dashboard_html_includes_component_health_section(client, monkeypatch):
    r = client.get("/fleet-dashboard")
    assert r.status_code == 200
    html = r.text
    assert "component-health" in html
    assert "component-health-item" in html
    assert "component-name" in html
    assert "component-status" in html
    assert "component-score" in html


def test_dashboard_html_includes_trend_analysis_section(client, monkeypatch):
    r = client.get("/fleet-dashboard")
    assert r.status_code == 200
    html = r.text
    assert "trend-analysis" in html
    assert "trend-item" in html
    assert "trend-metric" in html
    assert "trend-value" in html
    assert "trend-change" in html


def test_dashboard_html_includes_export_button(client, monkeypatch):
    r = client.get("/fleet-dashboard")
    assert r.status_code == 200
    html = r.text
    assert "export-data" in html
    assert "btn-outline" in html
    assert "EXPORT DATA" in html


def test_dashboard_html_includes_quick_actions(client, monkeypatch):
    r = client.get("/fleet-dashboard")
    assert r.status_code == 200
    html = r.text
    assert "quick-actions-btn" in html
    assert "⚡ QUICK ACTIONS" in html


def test_dashboard_html_includes_health_indicator(client, monkeypatch):
    r = client.get("/fleet-dashboard")
    assert r.status_code == 200
    html = r.text
    assert "health-indicator" in html
    assert "health-score" in html
    assert "critical-alerts" in html


def test_dashboard_export_functionality(client, monkeypatch):
    # This test would require mocking the export endpoint
    # For now, we'll just verify that the export button exists
    r = client.get("/fleet-dashboard")
    assert r.status_code == 200
    html = r.text
    assert "export-data" in html
    assert "btn-outline" in html
    assert "EXPORT DATA" in html


def test_dashboard_component_health_calculation(client, monkeypatch):
    monkeypatch.setattr(api, "_capacity_task_rows", lambda *a, **kw: [])
    monkeypatch.setattr(api, "build_capacity_forecast", lambda *a, **kw: {})
    monkeypatch.setattr(api, "build_allocation_plan", lambda *a, **kw: {})
    monkeypatch.setattr(api, "diagnose_incident", lambda *a, **kw: {})
    monkeypatch.setattr(api, "_self_healing", _FakeSelfHealing())
    monkeypatch.setattr(api, "_benchmark_controller", _FakeBenchCtrl())
    monkeypatch.setattr(api, "_recovery_control", _FakeRecovery())

    r = client.get("/api/fleet/dashboard")
    assert r.status_code == 200
    body = r.json()
    comp_health = body["component_health"]
    # Verify that component health scores are calculated correctly
    for component in comp_health:
        assert 0 <= component["score"] <= 100
        assert component["status"] in ["healthy", "warning", "critical", "degraded", "unknown"]


def test_dashboard_trend_analysis_calculation(client, monkeypatch):
    monkeypatch.setattr(api, "_capacity_task_rows", lambda *a, **kw: [])
    monkeypatch.setattr(api, "build_capacity_forecast", lambda *a, **kw: {})
    monkeypatch.setattr(api, "build_allocation_plan", lambda *a, **kw: {})
    monkeypatch.setattr(api, "diagnose_incident", lambda *a, **kw: {})
    monkeypatch.setattr(api, "_self_healing", _FakeSelfHealing())
    monkeypatch.setattr(api, "_benchmark_controller", _FakeBenchCtrl())
    monkeypatch.setattr(api, "_recovery_control", _FakeRecovery())

    r = client.get("/api/fleet/dashboard")
    assert r.status_code == 200
    body = r.json()
    trends = body["trend_data"]
    # Verify that trend data is calculated correctly
    for trend in trends:
        assert "metric" in trend
        assert "current" in trend
        assert "trend" in trend
        assert "severity" in trend
        assert trend["severity"] in ["critical", "warning", "stable"]
