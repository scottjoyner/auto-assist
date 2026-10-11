from __future__ import annotations

import json
import time

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from assistx.fleet_link_canary import (
    _approved_candidates,
    _canary_url,
    _ready_instances,
    build_fleet_link_canary_router,
)
from assistx.mobile_agent_routes import _mobile_model_handle


URL = "http://127.0.0.1:19341"
ROUTE = "destroyer/k2"
FINGERPRINT = "sha256:verified-test-artifact"
TRACE = "00-" + "1" * 32 + "-" + "a" * 16 + "-01"


def projection(*, expires=True, route=ROUTE, runtime="fleetlink:destroyer"):
    return {
        "source": "assistx",
        "schema_version": "2",
        "expires_at_ms": int(time.time() * 1000) + (60000 if expires else -60000),
        "providers": [{
            "enabled": True,
            "name": "approved-fleet-link",
            "runtime_instance_id": runtime,
            "runtime_kind": "openai_compatible",
            "type": "openai_compatible",
            "node_id": "destroyer",
            "base_url": URL + "/v1",
            "access_urls": [URL + "/v1"],
            "models": [{
                "alias": "K2",
                "provider_model": route,
                "artifact_fingerprint": FINGERPRINT,
                "capabilities": ["chat", "local_only"],
            }],
        }],
    }


def sidecar_ready(ready=True, *, response_model=ROUTE):
    return (
        {"schema": "fleet-model-link.v1", "generation": 7,
         "models": [{"id": ROUTE, "kind": "chat",
                     "ready_now": ready,
                     "status": "READY_PROVED_RECENTLY" if ready else "NOT_VERIFIED"}]},
        {"object": "list", "data": [{"id": ROUTE, "object": "model"}]},
    )


def gateway(monkeypatch, *, projection_doc=None, ready=True, auth=True,
            changed_result=None, state=None, do_network=True):
    monkeypatch.setenv("KIPNERTER_MOBILE_MODEL_HANDLE_SECRET", "disposable-test-secret")
    monkeypatch.setenv("ASSISTX_FLEET_LINK_CANARY", "1")
    monkeypatch.setenv("ASSISTX_FLEET_LINK_URL", URL)
    monkeypatch.setenv("ASSISTX_FLEET_LINK_RUNTIME_ID", "fleetlink:destroyer")
    events = [] if state is None else state
    model_doc = projection_doc if projection_doc is not None else projection()
    status, models = sidecar_ready(ready)
    def dispatch(request: httpx.Request):
        events.append({"path": request.url.path, "method": request.method,
                       "headers": dict(request.headers),
                       "body": request.content.decode()})
        if not do_network:
            raise httpx.ConnectError("synthetic failed connection")
        if request.url.path == "/fleet/nodes":
            return httpx.Response(200, json=status)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=models)
        if request.url.path == "/v1/chat/completions":
            request_body = json.loads(request.content)
            return httpx.Response(200, json={
                "model": changed_result if changed_result is not None else request_body["model"],
                "choices": [{
                    "message": {"role": "assistant", "content": "accepted synthetic canary"},
                    "finish_reason": "stop",
                }],
            }, headers={"X-Fleet-Trace-Id": "1" * 32})
        return httpx.Response(404)
    def client_factory():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(dispatch),
            follow_redirects=False,trust_env=False,
        )
    def auth_dependency():
        if not auth:
            raise HTTPException(status_code=401)
        return "operator"
    app = FastAPI()
    app.include_router(build_fleet_link_canary_router(
        auth_dependency,lambda: model_doc,client_factory))
    return TestClient(app),events


def test_preview_requires_two_live_proofs_and_approved_projection(monkeypatch):
    client, events = gateway(monkeypatch)
    result = client.get("/api/fleet-link/preview")
    assert result.status_code == 200
    doc = result.json()
    assert doc["mode"] == "OPERATOR_OBSERVATION_ONLY"
    assert doc["admitted_model_count"] == 1
    assert doc["ready_model_count"] == 1
    assert doc["execution_authorized"] is False
    assert [event["path"] for event in events] == ["/fleet/nodes", "/v1/models"]
    serialized = json.dumps(doc)
    for forbidden in (ROUTE, FINGERPRINT, "127.0.0.1", "fleetlink:destroyer", "node_id"):
        assert forbidden not in serialized


def test_operator_canary_request_forwards_exact_admitted_route_and_trace(monkeypatch):
    client, events = gateway(monkeypatch)
    handle = _mobile_model_handle(projection()["providers"][0]["models"][0])
    response = client.post("/api/fleet-link/canary",
        headers={"traceparent": TRACE},
        json={"model_handle": handle, "prompt": "just say synthetic", "max_tokens": 32})
    assert response.status_code == 200
    assert response.headers["X-Fleet-Trace-Id"] == "1" * 32
    result = response.json()
    assert result["model"] == handle
    assert result["canary_only"] is True
    assert result["no_automatic_dispatch"] is True
    assert result["choices"][0]["message"]["content"] == "accepted synthetic canary"
    assert [event["path"] for event in events] == [
        "/fleet/nodes", "/v1/models", "/v1/chat/completions"]
    sent = events[-1]
    assert json.loads(sent["body"]) == {
        "model": ROUTE, "stream": False, "max_tokens":32,
        "messages": [{"role": "user", "content": "just say synthetic"}],
    }
    assert sent["headers"]["traceparent"] == TRACE
    assert "127.0.0.1" not in response.text
    assert FINGERPRINT not in response.text
    assert ROUTE not in response.text


def test_disabled_does_not_even_open_network(monkeypatch):
    client,events = gateway(monkeypatch)
    monkeypatch.setenv("ASSISTX_FLEET_LINK_CANARY", "0")
    response = client.get("/api/fleet-link/preview")
    assert response.status_code == 200
    assert response.json()["ready_model_count"] == 0
    canary = client.post("/api/fleet-link/canary",
        json={"model_handle": "model:v1:" + "1"*32, "prompt": "hi"})
    assert canary.status_code == 503
    assert not events


def test_auth_dependency_blocks_both_routes_before_network(monkeypatch):
    client, events = gateway(monkeypatch,auth=False)
    assert client.get("/api/fleet-link/preview").status_code == 401
    assert client.post("/api/fleet-link/canary",
        json={"model_handle":"model:v1:"+"1"*32, "prompt":"hi"}).status_code==401
    assert not events


@pytest.mark.parametrize("url",[
    "http://localhost:19341","http://0.0.0.0:19341","http://127.0.0.2:19341",
    "http://127.0.0.1:19341/evil","http://127.0.0.1:19341?x=1",
    "http://operator@127.0.0.1:19341","https://127.0.0.1:19341",
    "http://127.0.0.1:80","http://127.0.0.1:19341#fragment",
    "http://127.0.0.1",
])
def test_nonliteral_loopback_endpoint_fails_before_network(monkeypatch,url):
    client, events = gateway(monkeypatch)
    monkeypatch.setenv("ASSISTX_FLEET_LINK_URL",url)
    assert client.get("/api/fleet-link/preview").json()["ready_model_count"] == 0
    assert events == []


def test_expired_signed_projection_does_not_reauthorize_old_route(monkeypatch):
    client,events=gateway(monkeypatch,projection_doc=projection(expires=False))
    handle=_mobile_model_handle(projection()["providers"][0]["models"][0])
    result=client.post("/api/fleet-link/canary",
       json={"model_handle":handle,"prompt":"hi"})
    assert result.status_code==503
    assert events == []


def test_inventory_only_does_not_confer_loaded_status(monkeypatch):
    client,events=gateway(monkeypatch,ready=False)
    handle=_mobile_model_handle(projection()["providers"][0]["models"][0])
    assert client.get("/api/fleet-link/preview").json()["ready_model_count"]==0
    assert client.post("/api/fleet-link/canary",
        json={"model_handle":handle,"prompt":"hi"}).status_code == 503
    assert all(event["method"]=="GET" for event in events)


def test_name_or_node_alias_cannot_replace_exact_approved_route(monkeypatch):
    client,events=gateway(monkeypatch,projection_doc=projection(route="destroyer/wrong"))
    handle=_mobile_model_handle(projection()["providers"][0]["models"][0])
    assert client.post("/api/fleet-link/canary",
        json={"model_handle":handle,"prompt":"hi"}).status_code==503
    assert all(event["method"]=="GET" for event in events)


def test_projection_runtime_id_must_match_explicit_pinned_provision(monkeypatch):
    client,events=gateway(monkeypatch,projection_doc=projection(runtime="other:localhost"))
    handle=_mobile_model_handle(projection()["providers"][0]["models"][0])
    assert client.post("/api/fleet-link/canary",
        json={"model_handle":handle,"prompt":"hi"}).status_code==503
    assert all(event["method"]=="GET" for event in events)


def test_no_exposure_for_wrong_model_or_untrusted_trace(monkeypatch):
    client,events=gateway(monkeypatch)
    for handle in ("model:v1:"+"0"*32,):
        response=client.post("/api/fleet-link/canary",json={"model_handle":handle,"prompt":"hi"})
        assert response.status_code==503
    response=client.post("/api/fleet-link/canary",
        headers={"traceparent":"00-"+"0"*32+"-"+"1"*16+"-01"},
        json={"model_handle":"model:v1:"+"1"*32,"prompt":"hi"})
    assert response.status_code==503
    assert all(event["method"]=="GET" for event in events)


def test_backend_identity_or_trace_mismatch_never_advertises_completion(monkeypatch):
    client, events = gateway(monkeypatch,changed_result="other-node/model")
    handle = _mobile_model_handle(projection()["providers"][0]["models"][0])
    response = client.post("/api/fleet-link/canary",
        json={"model_handle":handle,"prompt":"hi"})
    assert response.status_code == 503
    assert "other-node" not in response.text
    assert FINGERPRINT not in response.text


def test_network_failure_returns_sanitized_unavailable(monkeypatch):
    client, events = gateway(monkeypatch,do_network=False)
    result=client.get("/api/fleet-link/preview")
    assert result.status_code == 200
    assert result.json()["mode"]=="NOT_VERIFIED"
    handle=_mobile_model_handle(projection()["providers"][0]["models"][0])
    response=client.post("/api/fleet-link/canary",
        json={"model_handle":handle,"prompt":"hi"})
    assert response.status_code == 503
    assert response.json()=={"detail":{"error":"fleet_link_canary_unavailable"}}


def test_conflicting_same_handle_mappings_are_quarantined(monkeypatch):
    monkeypatch.setenv("KIPNERTER_MOBILE_MODEL_HANDLE_SECRET","disposable-test-secret")
    monkeypatch.setenv("ASSISTX_FLEET_LINK_RUNTIME_ID","fleetlink:destroyer")
    source=projection()
    prov=source["providers"][0]
    prov["models"].append({**prov["models"][0],"provider_model":"lenovo/lfm"})
    assert _approved_candidates(source,URL)=={}


def test_negative_no_wildcard_route_for_unknown_model(monkeypatch):
    monkeypatch.setenv("KIPNERTER_MOBILE_MODEL_HANDLE_SECRET","disposable-test-secret")
    monkeypatch.setenv("ASSISTX_FLEET_LINK_RUNTIME_ID","fleetlink:destroyer")
    source=projection(route="auto/local")
    # Even if a future router permits magic aliases, this is exact instance-only.
    assert _approved_candidates(source,URL)=={}


def test_projection_builder_v2_compatibility_is_same_signed_builder():
    from assistx.runtime_projection_v2 import (
        build_runtime_projection,
        build_runtime_projection_v2,
    )
    assert build_runtime_projection_v2 is build_runtime_projection
