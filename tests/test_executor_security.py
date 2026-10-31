from __future__ import annotations

import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.executor_security import (
    ExecutorAuthMiddleware,
    ExecutorTokenCodec,
    ExecutorTokenError,
)


def _claims(**overrides):
    now = int(time.time())
    value = {
        "iss": "assistx",
        "aud": ["assistx-executor", "auto-router"],
        "iat": now,
        "nbf": now,
        "exp": now + 300,
        "jti": "test-jti",
        "task_id": "task-1",
        "claim_id": "claim-1",
        "agent_id": "hermes-test",
        "projection_generation": 7,
        "scopes": ["context", "heartbeat", "complete", "inference"],
        "allowed_model_aliases": ["auto/code"],
        "allowed_tools": ["file"],
        "max_input_tokens": 4096,
        "max_output_tokens": 512,
        "max_attempts": 4,
    }
    value.update(overrides)
    return value


def _keys():
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private, public_pem


def test_executor_token_round_trip_and_tamper_rejection():
    private, _ = _keys()
    codec = ExecutorTokenCodec(private_key=private, key_id="test-key")
    token = codec.encode(_claims())
    decoded = codec.decode(token, audience="auto-router")
    assert decoded["task_id"] == "task-1"
    assert decoded["projection_generation"] == 7

    first, second, signature = token.split(".")
    tampered = f"{first}.{second[:-1]}A.{signature}"
    with pytest.raises(ExecutorTokenError, match="signature|JSON"):
        codec.decode(tampered, audience="auto-router")


def test_executor_token_rejects_expiry_and_wrong_audience():
    private, _ = _keys()
    codec = ExecutorTokenCodec(private_key=private, key_id="test-key")
    expired = codec.encode(_claims(exp=int(time.time()) - 1))
    with pytest.raises(ExecutorTokenError, match="expired"):
        codec.decode(expired, audience="auto-router")

    token = codec.encode(_claims())
    with pytest.raises(ExecutorTokenError, match="audience"):
        codec.decode(token, audience="untrusted-service")


async def _invoke(app, *, method: str, path: str, headers=None, body=b""):
    sent = []
    received = False

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_bootstrap_token_is_limited_to_poll_and_claim(monkeypatch):
    monkeypatch.setenv("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN", "bootstrap-secret")
    captured = {}

    async def downstream(scope, receive, send):
        captured["headers"] = dict(scope["headers"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = ExecutorAuthMiddleware(downstream)
    headers = [(b"authorization", b"Bearer bootstrap-secret")]
    sent = await _invoke(app, method="GET", path="/api/agent/tasks", headers=headers)
    assert sent[0]["status"] == 204
    assert captured["headers"][b"x-assistx-executor-identity"] == b"executor-bootstrap"

    denied = await _invoke(
        app,
        method="POST",
        path="/api/tasks/task-1/heartbeat",
        headers=headers,
        body=json.dumps({"task_id": "task-1"}).encode(),
    )
    assert denied[0]["status"] == 401


@pytest.mark.asyncio
async def test_task_token_is_fenced_to_task_and_scope(monkeypatch):
    private, public_pem = _keys()
    monkeypatch.setenv("ASSISTX_EXECUTOR_VERIFY_KEY_PEM", public_pem)
    monkeypatch.setenv("ASSISTX_EXECUTOR_KEY_ID", "test-key")
    token = ExecutorTokenCodec(private_key=private, key_id="test-key").encode(_claims())
    captured = {}

    async def downstream(scope, receive, send):
        captured["headers"] = dict(scope["headers"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = ExecutorAuthMiddleware(downstream)
    headers = [(b"authorization", f"Bearer {token}".encode())]
    ok = await _invoke(
        app,
        method="POST",
        path="/api/tasks/task-1/heartbeat",
        headers=headers,
        body=b"{}",
    )
    assert ok[0]["status"] == 204
    assert captured["headers"][b"x-assistx-executor-identity"].startswith(b"executor:hermes-test")

    wrong_task = await _invoke(
        app,
        method="POST",
        path="/api/tasks/task-2/complete",
        headers=headers,
        body=b"{}",
    )
    assert wrong_task[0]["status"] == 401


class _SyntheticExecutorClaimsDB:
    """Purely in-memory claim/projection fixture: no Neo4j or fleet connection."""

    def __init__(self, row):
        self.row = row

    def _session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def run(self, *_args, **_kwargs):
        return self

    def single(self):
        return self.row

    def close(self):
        return None


def _issue_synthetic_task_token(
    monkeypatch, payload, *, task_overrides=None, default_aliases=None,
):
    """Call the real token route, capture attempted signing without private keys."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from assistx.executor_security import build_executor_security_router

    monkeypatch.setenv("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN", "synthetic-bootstrap")
    monkeypatch.setenv("ASSISTX_EXECUTOR_ALLOWED_TOOLS", "terminal,file,memory")
    monkeypatch.setenv("ASSISTX_REQUIRE_OWNER_BINDING", "false")
    monkeypatch.delenv("ASSISTX_EXECUTOR_DEFAULT_MODEL_ALIASES", raising=False)
    if default_aliases is not None:
        monkeypatch.setenv("ASSISTX_EXECUTOR_DEFAULT_MODEL_ALIASES", default_aliases)

    now_ms = int(time.time() * 1000)
    task = {
        "status": "CLAIMED",
        "claim_id": "claim-1",
        "claimed_by": "hermes-test",
        "lease_expires_at_ts": now_ms + 120_000,
        "payload_json": payload,
    }
    task.update(task_overrides or {})
    row = {
        "task": task,
        "projection_generation": 7,
        "projection_status": "approved",
        "projection_expires_at_ts": now_ms + 180_000,
    }
    signed_claims = []

    class RecordingSigner:
        def encode(self, claims):
            signed_claims.append(dict(claims))
            return "synthetic-not-a-real-signature"

    monkeypatch.setattr(
        ExecutorTokenCodec,
        "signer_from_env",
        classmethod(lambda _cls: RecordingSigner()),
    )

    app = FastAPI()
    app.include_router(build_executor_security_router(lambda: _SyntheticExecutorClaimsDB(row)))
    response = TestClient(app).post(
        "/api/executor/claims/task-1/token",
        json={"agent_id": "hermes-test", "claim_id": "claim-1"},
        headers={"Authorization": "Bearer synthetic-bootstrap"},
    )
    return response, signed_claims


@pytest.mark.parametrize(
    "payload",
    [
        '{"model":',
        "[]",
        "null",
        '{"model":"model-a","model":"model-b"}',
        '{"model":"model-a","x":NaN}',
        '{"model":".."}',
        '{"model":"."}',
        '{"model":"../escape"}',
        '{"model":"/absolute"}',
        '{"model":"a//b"}',
        '{"model":"a%2Fb"}',
        '{"model":"a\\\\b"}',
        '{"model":"a\\u202e"}',
        '{"allowed_tools":null}',
        '{"allowed_tools":42}',
        '{"allowed_tools":["memory",12]}',
        '{"allowed_tools":"[broken"}',
        '{"allowed_model_aliases":[".."]}',
        '{"allowed_model_aliases":[]}',
        '{"execution_contract":"[broken"}',
    ],
)
def test_task_token_rejects_ambiguous_claimed_payload_without_signing(
    monkeypatch, payload,
):
    response, signed = _issue_synthetic_task_token(monkeypatch, payload)
    assert response.status_code == 409
    assert not signed


def test_canonical_corruption_never_falls_back_to_legacy_task_payload(monkeypatch):
    response, signed = _issue_synthetic_task_token(
        monkeypatch,
        '{"model":',
        task_overrides={"payload": '{"model":"approved","allowed_tools":["memory"]}'},
    )
    assert response.status_code == 409
    assert signed == []


@pytest.mark.parametrize("explicit_empty", [[], "", "[]"])
def test_explicit_empty_tools_do_not_expand_to_fleet_defaults(monkeypatch, explicit_empty):
    payload = json.dumps({"model": "approved/model", "allowed_tools": explicit_empty})
    response, signed = _issue_synthetic_task_token(monkeypatch, payload)
    assert response.status_code == 200
    assert response.json()["claims"]["allowed_tools"] == []
    assert len(signed) == 1
    assert signed[0]["allowed_tools"] == []


def test_absent_tools_use_documented_legacy_fleet_defaults(monkeypatch):
    response, signed = _issue_synthetic_task_token(
        monkeypatch, '{"model":"approved/model"}',
    )
    assert response.status_code == 200
    assert signed[0]["allowed_tools"] == ["file", "memory", "terminal"]
    assert signed[0]["allowed_model_aliases"] == ["approved/model"]


def test_requested_tools_are_intersected_with_operator_configuration(monkeypatch):
    response, signed = _issue_synthetic_task_token(
        monkeypatch,
        '{"model":"approved/model","allowed_tools":["memory","unconfigured"]}',
    )
    assert response.status_code == 200
    assert signed[0]["allowed_tools"] == ["memory"]


def test_malformed_list_never_grants_default_tools(monkeypatch):
    response, signed = _issue_synthetic_task_token(
        monkeypatch,
        '{"allowed_tools":[{"name":"memory"}]}',
    )
    assert response.status_code == 409
    assert not signed


def test_invalid_operator_default_model_alias_denies_token(monkeypatch):
    response, signed = _issue_synthetic_task_token(
        monkeypatch,
        '{"model":"approved/model"}',
        default_aliases="../untrusted",
    )
    assert response.status_code == 409
    assert not signed


def test_missing_canonical_payload_denies_even_with_valid_legacy_fallback(monkeypatch):
    response, signed = _issue_synthetic_task_token(
        monkeypatch,
        None,
        task_overrides={"payload": '{"model":"approved/model"}'},
    )
    assert response.status_code == 409
    assert not signed
