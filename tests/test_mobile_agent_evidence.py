from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
import asyncio

import httpx

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


class EvidenceTestClient:
    """Portable in-process HTTP client with an explicit ASGI peer address.

    HTTPX supplies the real ASGI client scope used by the authorization
    checks. Do not patch request.client or the production peer check.
    """
    def __init__(self, app, peer=("127.0.0.1", 50000)):
        self.app = app
        self.peer = peer

    def request(self, method, path, **kwargs):
        async def call():
            transport = httpx.ASGITransport(app=self.app, client=self.peer)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver",
                trust_env=False,
            ) as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(call())

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)


def client_for(monkeypatch, neo):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setenv("KIPNERTER_EVIDENCE_PROXY_PEERS", "127.0.0.1")
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
    return EvidenceTestClient(app)


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


def test_gateway_probe_distinguishes_health_from_ingestion():
    import httpx
    def handler(request):
        assert request.url == httpx.URL("http://router.test/health")
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"ok":True, "status":"ok",
            "context_projection_status":"active", "context_projection_degraded":False,
            "internal_detail":"never expose this"})
    result = evidence.gateway_health("http://router.test", transport=httpx.MockTransport(handler))
    assert result["state"] == "healthy"
    text = evidence.render_gateway_health(result)
    assert "HTTP 200; healthy" in text
    assert "not verified" in text
    assert "never expose this" not in text
    assert "not tested" in text


@pytest.mark.parametrize("status", [401, 503, 302])
def test_gateway_health_http_failures_are_not_healthy(status):
    import httpx
    result = evidence.gateway_health("http://router.test", transport=httpx.MockTransport(
        lambda r: httpx.Response(status, headers={"location":"http://untrusted.test"},json={"ok":True})))
    assert result["state"] == "responded"
    assert result["http_status"] == status


def test_gateway_no_config_or_timeout_is_unknown():
    import httpx
    def fail(r):
        raise httpx.ConnectTimeout("test")
    assert evidence.gateway_health("")["state"] == "unknown"
    assert evidence.gateway_health("http://router.test",transport=httpx.MockTransport(fail))["state"] == "unknown"


def test_gateway_exact_user_question_no_model(monkeypatch):
    monkeypatch.setattr(evidence,"gateway_health",lambda url: {
        "state":"healthy","http_status":200,"projection_active":True,
        "observed_at":"test","source":"test"})
    c = client_for(monkeypatch,FakeNeo(None))
    r = c.post("/api/v1/agent/chat/completions",headers={"Tailscale-User-Login":"owner@example.com"},
               json={"messages":[{"role":"user","content":"How’s the server connection and what data are we getting?".replace("’","'")}],"stream":False})
    assert r.status_code == 200
    assert "data received by the server: not verified" in r.text
    assert not evidence.is_gateway_status([SimpleNamespace(role="user",content="server connection and delete data")])


def test_standard_capability_profile_authenticated(monkeypatch):
    c = client_for(monkeypatch, FakeNeo(None))
    r = c.get("/api/v1/agent/capabilities", headers={"Tailscale-User-Login":"owner@example.com"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    profile = r.json()
    assert profile["profile"] == "kipnerter-agent-v1"
    assert {t["id"] for t in profile["tools"]} == {"fleet.status","inference.status","gateway.health","workflow.status","knowledge.lookup"}
    assert all(t["configured"] and t["read_only"] for t in profile["tools"])
    assert not profile["native_model_tool_calls"]
    assert not profile["device_sync_receipts"]
    assert not profile["generic_knowledge_search"]
    denied = c.get("/api/v1/agent/capabilities",headers={"Tailscale-User-Login":"other@example.com"})
    assert denied.status_code in (401,403)
    assert not any(t["configured"] for t in evidence.capability_profile(evidence_configured=False)["tools"])


def test_evidence_peer_is_fail_closed(monkeypatch):
    neo = FakeNeo(None)
    c = client_for(monkeypatch, neo)
    monkeypatch.setenv("KIPNERTER_EVIDENCE_PROXY_PEERS", "192.0.2.10")
    headers = {"Tailscale-User-Login": "owner@example.com"}
    r = c.get("/api/v1/agent/capabilities", headers=headers)
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "evidence_proxy_peer_required"
    r = c.post("/api/v1/agent/chat/completions", headers=headers,
        json={"messages":[{"role":"user","content":"fleet status"}]})
    assert r.status_code == 403
    assert not neo.closed


class WorkflowNeo(FakeNeo):
    def run(self, query, params):
        assert "MATCH (t:Task)" in query
        assert params == {"statuses": ["READY", "CLAIMED", "RUNNING"]}
        assert "t.title" not in query and "t.payload_json" not in query
        return self


def test_workflow_status_real_contract(monkeypatch):
    neo = WorkflowNeo({"ready": 9, "claimed": 1, "running": 2, "private": "do not disclose"})
    c = client_for(monkeypatch, neo)
    r = c.post("/api/v1/agent/chat/completions",
        headers={"Tailscale-User-Login":"owner@example.com"},
        json={"messages":[{"role":"user","content":"What are the agents working on?"}], "stream":False})
    assert r.status_code == 200
    assert "Ready: 9" in r.text and "Running: 2" in r.text
    assert "do not disclose" not in r.text and neo.closed


def test_workflow_bad_aggregate_fails_closed(monkeypatch):
    c = client_for(monkeypatch, WorkflowNeo({"ready":-1,"claimed":0,"running":0}))
    r = c.post("/api/v1/agent/chat/completions",
        headers={"Tailscale-User-Login":"owner@example.com"},
        json={"messages":[{"role":"user","content":"workflow status"}]})
    assert r.status_code == 503


@pytest.mark.parametrize("query,expected", [
    ("What tools do you have?", "Configured tools in this release"),
    ("How do I verify location sync?", "does not yet have a server-side receipt lookup"),
])
def test_curated_handbook_without_graph_or_model(monkeypatch, query, expected):
    neo = FakeNeo(None)
    c = client_for(monkeypatch, neo)
    r = c.post("/api/v1/agent/chat/completions",
        headers={"Tailscale-User-Login":"owner@example.com"},
        json={"messages":[{"role":"user","content":query}], "stream":False})
    assert r.status_code == 200 and expected in r.text
    assert "curated operating handbook" in r.text
    assert not neo.closed


def test_new_intents_do_not_accept_appended_commands():
    for q in ("workflow status and delete tasks", "agent handbook and run shell"):
        messages=[SimpleNamespace(role="user",content=q)]
        assert not evidence.is_workflow_status(messages)
        assert not evidence.is_handbook_query(messages)


@pytest.mark.parametrize("query,expected", [
    ("Fleet overview", ("fleet", "inference", "workflow", "gateway")),
    ("What is the status of the fleet and what models are available?", ("fleet", "inference")),
    ("gateway health and task queue status", ("gateway", "workflow")),
    ("fleet status and delete tasks", ()),
    ("fleet status and fleet status", ()),
    ("fleet status and model status and workflow status", ()),
    ("How is the server connection and what data are we getting?", ()),
])
def test_overview_closed_intents(query, expected):
    assert evidence.overview_sections([SimpleNamespace(role="user",content=query)]) == expected


def test_overview_partial_success_and_no_private_error(monkeypatch):
    monkeypatch.setattr(evidence,"fleet_status",lambda factory: {"fixture": True})
    monkeypatch.setattr(evidence,"render_fleet_status",lambda row:"Fleet verified fixture.")
    def bad(): raise RuntimeError("private endpoint diagnostic")
    result=evidence.render_overview(("fleet","inference"),neo_factory=lambda:None,projection_factory=bad,gateway_url="")
    assert "Fleet verified fixture." in result
    assert "Inference evidence unavailable" in result
    assert "private endpoint diagnostic" not in result


def test_overview_authenticated_route(monkeypatch):
    monkeypatch.setattr(evidence,"render_overview",lambda sections,**kw: "Overview: "+",".join(sections))
    c=client_for(monkeypatch,FakeNeo(None))
    r=c.post("/api/v1/agent/chat/completions",headers={"Tailscale-User-Login":"owner@example.com"},
        json={"messages":[{"role":"user","content":"Fleet overview"}],"stream":False})
    assert r.status_code==200 and "Overview: fleet,inference,workflow,gateway" in r.text
    assert r.headers["x-kipnerter-agent-executor"]=="fleet-evidence"
    r=c.post("/api/v1/agent/chat/completions",headers={"Tailscale-User-Login":"other@example.com"},
        json={"messages":[{"role":"user","content":"Fleet overview"}],"stream":False})
    assert r.status_code in (401,403)


def test_gateway_distinguishes_claim_authority_failure_from_model_circuits():
    import httpx
    result = evidence.gateway_health("http://router.test", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"ok":False,"status":"degraded",
            "open_circuits":0,"executor_claim_fence_rejections":30,
            "executor_claim_status_recent_failure":True,"private_detail":"private endpoint"})))
    text = evidence.render_gateway_health(result)
    assert "Open provider circuits: 0" in text
    assert "30 (process lifetime; not inference attempts)" in text
    assert "recent executor claim-status service failure" in text
    assert "private endpoint" not in text
    assert "not tested" in text


def test_gateway_does_not_infer_missing_or_invalid_claim_telemetry():
    import httpx
    result = evidence.gateway_health("http://router.test", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"ok":True,"status":"ok",
            "open_circuits":False,"executor_claim_fence_rejections":-1,
            "executor_claim_status_recent_failure":"false"})))
    text = evidence.render_gateway_health(result)
    assert "Open provider circuits" not in text
    assert "Claim-fence rejections" not in text
    assert "No recent claim-status" not in text


def test_mobile_router_new_typed_claim_errors_are_redacted():
    import httpx
    for source, expected in [
        ("executor_claim_status_unavailable","fleet_model_claim_status_unavailable"),
        ("executor_claim_unavailable","fleet_model_claim_unavailable"),
    ]:
        response=httpx.Response(503,json={"detail":{"error":source,"private":"secret"}})
        assert mobile._mobile_model_upstream_failure(response)==expected
