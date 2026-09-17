"""Tests for the fleet dashboard inference integration.

Verifies that /api/fleet/dashboard includes the inference_state field and
that the fleet_dashboard.html template renders the inference section.
"""
import pytest
from fastapi.testclient import TestClient

import assistx.api as api
from assistx.api import app
from assistx.llm import client as llm_client


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


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
    def single(self):
        return None

    def __iter__(self):
        return iter([])

    def consume(self):
        pass


class _FakeHealthPlan:
    pass


class _FakeValueMatrix:
    pass


# ─── Dashboard API includes inference_state ─────────────────────────────────

def test_dashboard_includes_inference_state(client, monkeypatch):
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
    assert "inference_state" in body
    inf = body["inference_state"]
    assert "active_sessions" in inf
    assert "models_active" in inf
    assert "session_count" in inf
    assert inf["session_count"] == 0


def test_dashboard_inference_state_reflects_sessions(client, monkeypatch):
    monkeypatch.setattr(api, "_capacity_task_rows", lambda *a, **kw: [])
    monkeypatch.setattr(api, "build_capacity_forecast", lambda *a, **kw: {})
    monkeypatch.setattr(api, "build_allocation_plan", lambda *a, **kw: {})
    monkeypatch.setattr(api, "diagnose_incident", lambda *a, **kw: {})
    monkeypatch.setattr(api, "_self_healing", _FakeSelfHealing())
    monkeypatch.setattr(api, "_benchmark_controller", _FakeBenchCtrl())
    monkeypatch.setattr(api, "_recovery_control", _FakeRecovery())

    # Register 2 active sessions before calling dashboard
    llm_client.register_inference_start("qwen", "http://x:1234/v1", "n1")
    llm_client.register_inference_start("qwen", "http://x:1234/v1", "n1")
    llm_client.register_inference_start("llama", "http://y:1234/v1", "n2")

    r = client.get("/api/fleet/dashboard")
    inf = r.json()["inference_state"]
    assert inf["session_count"] == 3
    assert len(inf["models_active"]) == 2


# ─── Fleet dashboard HTML template ─────────────────────────────────────────

def test_fleet_dashboard_html_includes_inference_section(client, monkeypatch):
    r = client.get("/fleet-dashboard")
    assert r.status_code == 200
    html = r.text
    assert "section-inference-state" in html
    assert "inference-state" in html
    assert "fleet_dashboard.js" in html


# ─── Fleet dashboard HTML includes all key sections ─────────────────────────

def test_fleet_dashboard_html_has_all_sections(client, monkeypatch):
    r = client.get("/fleet-dashboard")
    html = r.text
    for section_id in [
        "section-nodes", "section-model-matrix", "section-routing",
        "section-routing-regret", "section-loadout-simulation",
        "section-capacity-forecast", "section-self-healing",
        "section-benchmarks", "section-tasks", "section-completions",
        "section-service-health", "section-hardware", "section-inference-state",
    ]:
        assert section_id in html, f"missing section {section_id}"


# ─── Helper stubs ──────────────────────────────────────────────────────────

class _FakeSelfHealing:
    def status(self):
        return {"automatic_quarantine": False}


class _FakeBenchCtrl:
    def status(self):
        return {"enabled": False}


class _FakeRecovery:
    def status(self):
        return {"execution_enabled": False}
