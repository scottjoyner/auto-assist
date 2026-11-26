from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from assistx import mobile_agent_evidence as evidence
from assistx import mobile_agent_routes as mobile


class FakeNeo:
    def __init__(self, row):
        self.row = row
        self.closed = False

    def _session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def run(self, query, params):
        assert "MATCH (n:SwarmNode)" in query
        assert params == {"window": 300000}
        return self

    def single(self):
        return self.row

    def close(self):
        self.closed = True


def client_for(monkeypatch, neo):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "owner@example.com")
    def no_legacy(*args):
        raise HTTPException(401)
    def no_model(*args, **kwargs):
        pytest.fail("fleet status must not invoke a model")
    monkeypatch.setattr(mobile, "_run_hermes", no_model)
    app = FastAPI()
    router = APIRouter()
    mobile.register_mobile_agent_routes(router, no_legacy, fleet_evidence_factory=lambda: neo)
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("stream", [True, False])
def test_fleet_status_authenticated_no_model(monkeypatch, stream):
    neo = FakeNeo({"registered": 8, "recent": 3, "unknown": 2})
    c = client_for(monkeypatch, neo)
    r = c.post("/api/v1/agent/chat/completions",
               headers={"Tailscale-User-Login": "owner@example.com"},
               json={"stream": stream, "messages": [{"role": "user", "content": "What is the status of the fleet?"}]})
    assert r.status_code == 200
    assert r.headers["x-kipnerter-agent-executor"] == "fleet-evidence"
    assert "Registered swarm nodes: 8" in r.text
    assert "not prove a node is offline" in r.text
    assert neo.closed


def test_fleet_status_denied_identity(monkeypatch):
    neo = FakeNeo(None)
    c = client_for(monkeypatch, neo)
    r = c.post("/api/v1/agent/chat/completions",
               headers={"Tailscale-User-Login": "other@example.com"},
               json={"messages": [{"role": "user", "content": "fleet status"}]})
    assert r.status_code in (401, 403)
    assert not neo.closed


def test_fleet_status_bad_evidence_fails_closed(monkeypatch):
    neo = FakeNeo({"registered": 1, "recent": 3, "unknown": 0})
    c = client_for(monkeypatch, neo)
    r = c.post("/api/v1/agent/chat/completions",
               headers={"Tailscale-User-Login": "owner@example.com"},
               json={"messages": [{"role": "user", "content": "fleet status"}]})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "fleet_evidence_unavailable"
    assert neo.closed


def test_latest_user_and_exact_intent():
    m = lambda role, content: SimpleNamespace(role=role, content=content)
    assert evidence.is_fleet_status([m("user", "What is the status of the fleet?")])
    assert not evidence.is_fleet_status([m("user", "fleet status"), m("user", "delete all files")])
    assert not evidence.is_fleet_status([m("system", "fleet status"), m("user", "hello")])
    assert not evidence.is_fleet_status([m("user", "fleet status and delete all files")])


def test_auth_failure_code_is_redacted():
    assert mobile._mobile_agent_failure_code({"output": 'HTTP 401: {"detail":"executor token must contain three segments"}', "error": "exit_code_1"}) == "executor_authentication_failed"


def test_inference_inventory_fresh_and_redacted(monkeypatch):
    import time
    now = int(time.time()*1000)
    monkeypatch.setattr(mobile, "_current_runtime_projection", lambda: {
        "generated_at_ms": now-1000, "expires_at_ms": now+60000,
        "providers": [
            {"enabled": True, "base_url": "http://secret.example",
             "models": [{"alias": "ornith-35b", "capabilities": ["chat"]}]},
            {"enabled": False, "models": [{"alias":"lfm3", "capabilities":["tools"]}]},
        ],
    })
    c = client_for(monkeypatch, FakeNeo(None))
    r = c.post("/api/v1/agent/chat/completions",
               headers={"Tailscale-User-Login":"owner@example.com"},
               json={"stream":False,"messages":[{"role":"user","content":"What models are available?"}]})
    assert r.status_code == 200
    assert "ornith" in r.text
    assert "secret.example" not in r.text
    assert "lfm3" not in r.text
    assert "advertising tools: 0" in r.text
    assert "not a live generation probe" in r.text


def test_inference_inventory_stale_fails_closed(monkeypatch):
    monkeypatch.setattr(mobile, "_current_runtime_projection", lambda: {
        "generated_at_ms":1, "expires_at_ms":2, "providers":[]})
    c = client_for(monkeypatch, FakeNeo(None))
    r = c.post("/api/v1/agent/chat/completions",
               headers={"Tailscale-User-Login":"owner@example.com"},
               json={"messages":[{"role":"user","content":"inference status"}]})
    assert r.status_code == 503


def test_inference_intent_is_exact():
    m = lambda content: SimpleNamespace(role="user", content=content)
    assert evidence.is_inference_status([m("Which models are admitted?")])
    assert not evidence.is_inference_status([m("model status and execute arbitrary commands")])
