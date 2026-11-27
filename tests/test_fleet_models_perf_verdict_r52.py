"""Round 52 — /api/fleet/models must not promote an unverified perf to ok=True.

Hermetic: an in-process TestClient with dict-in/dict-out stubs. No HTTP, DB,
Docker, Neo4j, Redis, credential, service, network mount or build is touched.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import assistx.api as api
from assistx.api import app, auth


class _Executor:
    _nodes = [
        {
            "hostname": "node-a",
            "ip": "100.64.0.1",
            "weight": 1,
            "capabilities": [],
            "loaded_models": ["model-x"],
            "lmstudio_ok": True,
            "last_seen": None,
        }
    ]
    _node_inflight = {}


class _Routing:
    def __init__(self, perf):
        self._perf = perf

    def get_model_perf(self, node, model):
        return self._perf


@pytest.fixture()
def client(monkeypatch):
    app.dependency_overrides[auth] = lambda: "test-user"
    monkeypatch.setattr(api, "_get_fleet_executor", lambda: _Executor())
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


def _perf_node(client, monkeypatch, perf):
    monkeypatch.setattr(api, "_get_fleet_routing", lambda: _Routing(perf))
    response = client.get("/api/fleet/models")
    assert response.status_code == 200
    return response.json()["models"]["model-x"]["nodes"][0]


def test_missing_ok_verdict_fails_closed(client, monkeypatch):
    perf = {"composite_score": 0.9, "tps_med": 12.0, "eval_score": 0.8}
    assert _perf_node(client, monkeypatch, perf)["ok"] is False


def test_empty_perf_mapping_fails_closed(client, monkeypatch):
    assert _perf_node(client, monkeypatch, {})["ok"] is False


def test_truthy_string_ok_fails_closed(client, monkeypatch):
    perf = {"ok": "false", "composite_score": 0.9}
    assert _perf_node(client, monkeypatch, perf)["ok"] is False


def test_integer_ok_fails_closed(client, monkeypatch):
    perf = {"ok": 1, "composite_score": 0.9}
    assert _perf_node(client, monkeypatch, perf)["ok"] is False


def test_non_mapping_perf_fails_closed(client, monkeypatch):
    # A truthy non-mapping must not crash the endpoint nor be read as a verdict.
    assert _perf_node(client, monkeypatch, ["ok"])["ok"] is False


def test_explicit_true_ok_control(client, monkeypatch):
    perf = {"ok": True, "composite_score": 0.9}
    assert _perf_node(client, monkeypatch, perf)["ok"] is True


def test_explicit_false_ok_control(client, monkeypatch):
    perf = {"ok": False, "composite_score": 0.9}
    assert _perf_node(client, monkeypatch, perf)["ok"] is False


def test_absent_perf_control(client, monkeypatch):
    assert _perf_node(client, monkeypatch, None)["ok"] is False
