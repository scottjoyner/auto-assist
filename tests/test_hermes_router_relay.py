"""Offline, mock-transport tests for opt-in private Hermes text-chat relay."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistx import hermes_router_relay as relay

CLIENT_TOKEN = "hermes-only-client-token-" + "a" * 40
UPSTREAM_TOKEN = "router-service-only-token-" + "b" * 40
PATH = "/api/internal/hermes-router/v1/chat/completions"
VALID = {
    "model": "synthetic-model",
    "messages": [{"role": "user", "content": "synthetic only"}],
    "max_tokens": 16, "stream": False,
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ASSISTX_HERMES_ROUTER_RELAY_ENABLED", "true")
    monkeypatch.setenv("ASSISTX_HERMES_RELAY_CLIENT_TOKEN", CLIENT_TOKEN)
    monkeypatch.setenv("ASSISTX_INTERNAL_SERVICE_TOKEN", UPSTREAM_TOKEN)
    monkeypatch.setenv("ASSISTX_HERMES_RELAY_ALLOWED_MODELS", "synthetic-model")
    app = FastAPI()
    app.include_router(relay.router)
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


def request(client, body=None, headers=None):
    headers = {
        "Authorization": "Bearer " + CLIENT_TOKEN,
        "Content-Type": "application/json",
        **(headers or {}),
    }
    return client.post(PATH, content=json.dumps(VALID if body is None else body),
                       headers=headers)


def mock_router(monkeypatch, handler):
    requests = []

    def observer(req: httpx.Request):
        requests.append(req)
        return handler(req)

    monkeypatch.setattr(
        relay, "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(observer),
            trust_env=False, follow_redirects=False,
        ),
    )
    return requests


def test_disabled_by_default_rejects_before_upstream_or_body(client, monkeypatch):
    monkeypatch.delenv("ASSISTX_HERMES_ROUTER_RELAY_ENABLED")
    assert request(client).status_code == 404
    monkeypatch.setenv("ASSISTX_HERMES_ROUTER_RELAY_ENABLED", "false")
    assert request(client).status_code == 404


def test_missing_weak_or_shared_admission_tokens_fail_closed(client, monkeypatch):
    monkeypatch.setenv("ASSISTX_HERMES_RELAY_CLIENT_TOKEN", "weak")
    assert request(client).status_code == 503
    monkeypatch.setenv("ASSISTX_HERMES_RELAY_CLIENT_TOKEN", UPSTREAM_TOKEN)
    assert request(client).status_code == 503


@pytest.mark.parametrize("supplied", ["", "Bearer bad", "Basic abc", "Bearer "])
def test_unauthorized_cannot_touch_relay(client, monkeypatch, supplied):
    invoked = mock_router(
        monkeypatch, lambda _: pytest.fail("unauthorized transport called"),
    )
    assert request(client, headers={"Authorization": supplied}).status_code == 401
    assert invoked == []


def test_narrow_forwarding_and_header_separation(client, monkeypatch):
    def upstream(req):
        assert str(req.url) == "http://llm-router:8088/v1/chat/completions"
        assert req.method == "POST"
        assert req.headers["Authorization"] == "Bearer " + UPSTREAM_TOKEN
        assert CLIENT_TOKEN not in json.dumps(dict(req.headers))
        assert "cookie" not in req.headers
        assert "x-admin-token" not in req.headers
        assert json.loads(req.content) == VALID
        return httpx.Response(200, headers={"Content-Type": "application/json"},
                              json={"choices": [{"message": {"content": "ok"}}]})
    invoked = mock_router(monkeypatch, upstream)
    answer = request(
        client,
        headers={
            "X-Admin-Token": "never-forward-this",
            "Cookie": "private=never-forward",
        },
    )
    assert answer.status_code == 200
    assert answer.json()["choices"][0]["message"]["content"] == "ok"
    assert len(invoked) == 1
    assert answer.headers["Cache-Control"] == "no-store"


def test_streaming_sse_is_relayed_without_header_leak(client, monkeypatch):
    def backend(_):
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"},
            content=b'data: {"choices": []}\n\ndata: [DONE]\n\n',
        )
    invoked = mock_router(monkeypatch, backend)
    body = {**VALID, "stream": True}
    answer = request(client, body)
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("text/event-stream")
    assert b"data: [DONE]" in answer.content
    assert len(invoked) == 1


@pytest.mark.parametrize(("body", "status"), [
    ({"model": "unapproved", "messages": VALID["messages"]}, 403),
    ({"model": "synthetic-model", "messages": [], "stream": False}, 422),
    ({**VALID, "url": "http://evil.invalid/admin"}, 422),
    ({**VALID, "tools": [{"type": "function"}]}, 422),
    ({**VALID, "max_tokens": 200000}, 422),
    ({**VALID, "temperature": True}, 422),
    ({**VALID, "stream": "yes"}, 422),
    ({**VALID, "messages": [{"role": "tool", "content": "privileged"}]}, 422),
])
def test_rejects_unapproved_models_fields_and_scope(client, monkeypatch, body, status):
    invoked = mock_router(monkeypatch, lambda _: pytest.fail("unexpected network"))
    assert request(client, body).status_code == status
    assert invoked == []


def test_oversized_body_and_duplicate_json_denied_without_backend(client, monkeypatch):
    invoked = mock_router(monkeypatch, lambda _: pytest.fail("unexpected network"))
    big = {**VALID, "messages": [
        {"role": "user", "content": "a" * 65536}
    ]}
    assert request(client, big).status_code == 413
    headers = {"Authorization": "Bearer " + CLIENT_TOKEN,
               "Content-Type": "application/json"}
    doubled = '{"model":"synthetic-model","model":"synthetic-model","messages":[{"role":"user","content":"ok"}]}'
    answer = client.post(PATH, content=doubled, headers=headers)
    assert answer.status_code == 400
    assert invoked == []


def test_redirect_upstream_errors_and_media_type_deny(client, monkeypatch):
    for result in (
        httpx.Response(302, headers={"Location": "https://elsewhere.invalid"}),
        httpx.Response(401, text="inadvertent private token"),
        httpx.Response(200, headers={"Content-Type": "text/html"},
                       text="untrusted"),
    ):
        mock_router(monkeypatch, lambda _req, result=result: result)
        answer = request(client)
        assert answer.status_code == 502
        assert "private token" not in answer.text
        assert "elsewhere.invalid" not in answer.text


def test_missing_model_allowlist_and_query_string_fail_closed(client, monkeypatch):
    invoked = mock_router(monkeypatch, lambda _: pytest.fail("unexpected network"))
    monkeypatch.delenv("ASSISTX_HERMES_RELAY_ALLOWED_MODELS")
    assert request(client).status_code == 503
    monkeypatch.setenv("ASSISTX_HERMES_RELAY_ALLOWED_MODELS", "synthetic-model")
    hdr = {"Authorization": "Bearer " + CLIENT_TOKEN,
           "Content-Type": "application/json"}
    assert client.post(PATH + "?target=/admin", json=VALID, headers=hdr).status_code == 400
    assert invoked == []


def test_unsupported_methods_and_paths_not_mounted(client):
    hdr = {"Authorization": "Bearer " + CLIENT_TOKEN}
    assert client.get("/api/internal/hermes-router/v1/models", headers=hdr).status_code == 404
    assert client.post("/api/internal/hermes-router/v1/admin/providers",
                       headers=hdr).status_code == 404


def test_transport_failure_is_sanitized(client, monkeypatch):
    def broken(_):
        raise httpx.ConnectError("dangerous-private-address-and-secret")
    mock_router(monkeypatch, broken)
    answer = request(client)
    assert answer.status_code == 503
    assert "dangerous" not in answer.text
