"""Tests for the /api/fleet/inference/* endpoints.

Uses FastAPI TestClient with auth monkeypatched out.  The discovery validation
in /start is also patched so we test logic without hitting real LM Studio nodes.
"""
import os
import time

import pytest
from fastapi.testclient import TestClient

import assistx.api as api
from assistx.api import app
from assistx.llm import client as llm_client


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def _clear_inference_state():
    llm_client._inference_sessions.clear()
    llm_client._inference_session_id = 0


@pytest.fixture(autouse=True)
def _isolate():
    _clear_inference_state()
    yield
    _clear_inference_state()


@pytest.fixture()
def client(monkeypatch):
    from assistx.api import auth
    app.dependency_overrides[auth] = lambda: "test-user"
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


# ─── POST /api/fleet/inference/start ────────────────────────────────────────

def test_start_inference_success(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    r = client.post("/api/fleet/inference/start", json={
        "model_id": "qwen-7b",
        "base_url": "http://10.0.0.1:1234/v1",
        "node_id": "beelink",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["session_id"].startswith("inf_")


def test_start_inference_missing_model_id(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    r = client.post("/api/fleet/inference/start", json={
        "base_url": "http://10.0.0.1:1234/v1",
    })
    assert r.status_code == 400


def test_start_inference_missing_base_url(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    r = client.post("/api/fleet/inference/start", json={
        "model_id": "qwen-7b",
    })
    assert r.status_code == 400


def test_start_inference_validation_passes_when_model_discovered(client, monkeypatch):
    def fake_get(url, timeout=4):
        return _FakeResp(200, {"data": [{"id": "qwen-7b"}, {"id": "other"}]})
    monkeypatch.setattr(api.requests, "get", fake_get)
    r = client.post("/api/fleet/inference/start", json={
        "model_id": "qwen-7b",
        "base_url": "http://10.0.0.1:1234/v1",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_start_inference_registers_even_if_node_unreachable(client, monkeypatch):
    def fake_get(url, timeout=4):
        raise ConnectionError("node unreachable")
    monkeypatch.setattr(api.requests, "get", fake_get)
    r = client.post("/api/fleet/inference/start", json={
        "model_id": "qwen-7b",
        "base_url": "http://10.0.0.1:1234/v1",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ─── POST /api/fleet/inference/stop ─────────────────────────────────────────

def test_stop_inference_success(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    sid = client.post("/api/fleet/inference/start", json={
        "model_id": "qwen-7b", "base_url": "http://10.0.0.1:1234/v1",
    }).json()["session_id"]
    r = client.post("/api/fleet/inference/stop", json={"session_id": sid})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["session"]["status"] == "completed"


def test_stop_inference_nonexistent(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    r = client.post("/api/fleet/inference/stop", json={"session_id": "inf_bogus"})
    assert r.status_code == 404


def test_stop_inference_missing_session_id(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    r = client.post("/api/fleet/inference/stop", json={})
    assert r.status_code == 400


def test_stop_inference_custom_status(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    sid = client.post("/api/fleet/inference/start", json={
        "model_id": "m", "base_url": "http://x:1234/v1",
    }).json()["session_id"]
    r = client.post("/api/fleet/inference/stop", json={"session_id": sid, "status": "timeout"})
    assert r.status_code == 200
    assert r.json()["session"]["status"] == "timeout"


# ─── POST /api/fleet/inference/stop-model ───────────────────────────────────

def test_stop_model_success(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    for _ in range(3):
        client.post("/api/fleet/inference/start", json={
            "model_id": "a", "base_url": "http://x:1234/v1",
        })
    r = client.post("/api/fleet/inference/stop-model", json={
        "model_id": "a", "base_url": "http://x:1234/v1",
    })
    assert r.status_code == 200
    assert r.json()["sessions_stopped"] == 3


def test_stop_model_missing_params(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    r = client.post("/api/fleet/inference/stop-model", json={"model_id": "a"})
    assert r.status_code == 400
    r = client.post("/api/fleet/inference/stop-model", json={"base_url": "http://x:1234/v1"})
    assert r.status_code == 400


def test_stop_model_no_matches(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    r = client.post("/api/fleet/inference/stop-model", json={
        "model_id": "nonexistent", "base_url": "http://x:1234/v1",
    })
    assert r.status_code == 200
    assert r.json()["sessions_stopped"] == 0


# ─── GET /api/fleet/inference/sessions ──────────────────────────────────────

def test_sessions_empty(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    r = client.get("/api/fleet/inference/sessions")
    assert r.status_code == 200
    assert r.json()["count"] == 0
    assert r.json()["sessions"] == []


def test_sessions_lists_active(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    client.post("/api/fleet/inference/start", json={
        "model_id": "a", "base_url": "http://x:1234/v1",
    })
    r = client.get("/api/fleet/inference/sessions")
    assert r.json()["count"] == 1
    assert r.json()["sessions"][0]["model_id"] == "a"


def test_sessions_filter_model(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    client.post("/api/fleet/inference/start", json={"model_id": "a", "base_url": "http://x:1234/v1"})
    client.post("/api/fleet/inference/start", json={"model_id": "b", "base_url": "http://x:1234/v1"})
    r = client.get("/api/fleet/inference/sessions", params={"model_id": "a"})
    assert r.json()["count"] == 1


def test_sessions_only_active_false(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    sid = client.post("/api/fleet/inference/start", json={
        "model_id": "a", "base_url": "http://x:1234/v1",
    }).json()["session_id"]
    client.post("/api/fleet/inference/stop", json={"session_id": sid})
    r = client.get("/api/fleet/inference/sessions", params={"only_active": "false"})
    assert r.json()["count"] == 1


# ─── GET /api/fleet/inference/models ────────────────────────────────────────

def test_models_empty(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    r = client.get("/api/fleet/inference/models")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_models_groups_sessions(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    client.post("/api/fleet/inference/start", json={"model_id": "qwen", "base_url": "http://x:1234/v1"})
    client.post("/api/fleet/inference/start", json={"model_id": "qwen", "base_url": "http://x:1234/v1"})
    r = client.get("/api/fleet/inference/models")
    assert r.json()["count"] == 1
    assert r.json()["models"][0]["active_sessions"] == 2


def test_models_excludes_stopped(client, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **kw: _FakeResp(404, {}))
    sid = client.post("/api/fleet/inference/start", json={
        "model_id": "qwen", "base_url": "http://x:1234/v1",
    }).json()["session_id"]
    client.post("/api/fleet/inference/stop", json={"session_id": sid})
    r = client.get("/api/fleet/inference/models")
    assert r.json()["count"] == 0
