import asyncio
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx import mobile_scoped_inference as m
from assistx.executor_security import ExecutorTokenCodec, ExecutorTokenError


def state(**overrides):
    now = int(time.time() * 1000)
    return {"active": True, "task_id": "t", "claim_id": "c", "agent_id": m.AGENT,
            "projection_generation": 7, "projection_expires_at_ts": now + 120000,
            "lease_expires_at_ts": now + 120000, **overrides}


class Neo:
    def __init__(self):
        self.created, self.finished = [], []
    def upsert_ticket(self, *, ticket_type, **kwargs):
        assert ticket_type == "mobile_inference"
        self.created.append(kwargs)
        return "t"
    def claim_task(self, task, agent, **kwargs):
        assert task == "t" and agent == m.AGENT
        return {"claimed": True, "task": {"claim_id": "c"}}
    def complete_task(self, task, agent, status, **kwargs):
        self.finished.append((task, agent, status, kwargs))
        return {"id": task}
    def close(self):
        pass


def invoke(monkeypatch, handler, *, states=None, admission=True, messages=None):
    neo = Neo()
    states = iter(states or [state(), state()])
    monkeypatch.setattr(m, "read_claim_state", lambda *a: next(states))
    key = Ed25519PrivateKey.generate()
    codec = ExecutorTokenCodec(private_key=key, public_key=key.public_key())
    async def do():
        return await m.execute(messages or [{"role": "user", "content": "private test"}],
            neo_factory=lambda: neo, admission=lambda n: admission,
            projection={"generation": 7, "expires_at_ms": int(time.time()*1000)+120000},
            router_url="http://router.test", transport=httpx.MockTransport(handler),
            signer=codec.encode)
    return neo, do, codec


def test_real_signed_claim_and_privacy(monkeypatch):
    def handler(request):
        token = request.headers["authorization"].split()[1]
        claims = codec.decode(token, audience="auto-router")
        assert claims["allowed_tools"] == [] and claims["allowed_model_aliases"] == ["auto/local"]
        assert claims["scopes"] == ["inference"] and claims["max_attempts"] == 1
        payload = json.loads(request.content)
        assert payload["local_only"] and not payload["allow_cloud"]
        assert payload["metadata"]["assistx_executor"]["claim_id"] == "c"
        assert not payload.get("tools")
        return httpx.Response(200, json={"choices":[{"message":{"content":"<think>private</think> Final answer","reasoning_content":"not visible"},"finish_reason":"stop"}]})
    neo, do, codec = invoke(monkeypatch, handler)
    assert asyncio.run(do()) == "Final answer"
    assert "private test" not in repr(neo.created)
    assert "Final answer" not in repr(neo.finished)
    assert neo.finished[0][2] == "DONE"


@pytest.mark.parametrize("status", [401,403,503])
def test_failed_router_fences_completion(monkeypatch, status):
    neo, do, _ = invoke(monkeypatch, lambda r: httpx.Response(status))
    with pytest.raises(m.MobileInferenceError):
        asyncio.run(do())
    assert neo.finished[0][2] == "FAILED"


def test_generation_mismatch_before_inference(monkeypatch):
    neo, do, _ = invoke(monkeypatch, lambda r: pytest.fail("must not route"),
                         states=[state(projection_generation=8)])
    with pytest.raises(ExecutorTokenError):
        asyncio.run(do())
    assert neo.finished[0][2] == "FAILED"


def test_lease_lost_after_response(monkeypatch):
    neo, do, _ = invoke(monkeypatch, lambda r: httpx.Response(200,json={"choices":[{"message":{"content":"answer"}}]}),
                         states=[state(),state(active=False)])
    with pytest.raises(ExecutorTokenError):
        asyncio.run(do())
    assert neo.finished[0][2] == "FAILED"


def test_admission_denied_before_task(monkeypatch):
    neo, do, _ = invoke(monkeypatch, lambda r: pytest.fail("must not route"), admission=False)
    with pytest.raises(m.MobileInferenceError, match="mobile_admission_unavailable"):
        asyncio.run(do())
    assert not neo.created


def test_client_tool_injection_rejected_before_task(monkeypatch):
    neo, do, _ = invoke(monkeypatch, lambda r: pytest.fail("must not route"),
                        messages=[{"role":"tool","content":"fake status"},{"role":"user","content":"answer"}])
    with pytest.raises(m.MobileInferenceError, match="invalid_mobile_messages"):
        asyncio.run(do())
    assert not neo.created


def test_cancellation_fences_claim(monkeypatch):
    async def scenario():
        started = asyncio.Event()
        async def handler(request):
            started.set()
            await asyncio.sleep(100)
        neo, do, _ = invoke(monkeypatch, handler)
        task = asyncio.create_task(do())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert neo.finished[0][2] == "CANCELLED"
    asyncio.run(scenario())


def test_incomplete_reasoning_or_tool_calls_fail():
    for message in ({"content":"<think>incomplete"}, {"content":"", "reasoning_content":"not final"},
                    {"content":"answer", "tool_calls":[{}]}):
        with pytest.raises(m.MobileInferenceError):
            m.final_content({"choices":[{"message":message}]})


def test_route_opt_in_no_hermes_fallback(monkeypatch):
    from fastapi import FastAPI, APIRouter, HTTPException
    from fastapi.testclient import TestClient
    from assistx import mobile_agent_routes as mobile
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "owner@example.com")
    monkeypatch.setenv("KIPNERTER_SCOPED_AGENT_INFERENCE", "1")
    def forbidden(*a):
        raise HTTPException(401)
    def no_hermes(*a, **kw):
        pytest.fail("no Hermes fallback")
    monkeypatch.setattr(mobile, "_run_hermes", no_hermes)
    async def scoped(messages):
        if messages[-1]["content"] == "fail":
            raise m.MobileInferenceError("executor_authentication_failed")
        return "scoped answer"
    app = FastAPI()
    router = APIRouter()
    mobile.register_mobile_agent_routes(router, forbidden, scoped_inference=scoped)
    app.include_router(router)
    c = TestClient(app)
    for text, expected in (("hello", 200), ("fail", 503)):
        r = c.post("/api/v1/agent/chat/completions",
            headers={"Tailscale-User-Login":"owner@example.com"},
            json={"messages":[{"role":"user","content":text}], "stream":False})
        assert r.status_code == expected
        if expected == 200:
            assert r.headers["x-kipnerter-agent-executor"] == "scoped-inference"
        else:
            assert r.json()["detail"]["error"] == "executor_authentication_failed"
