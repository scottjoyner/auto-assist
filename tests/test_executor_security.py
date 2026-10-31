from __future__ import annotations

import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.claim_binding import (
    ClaimBindingError,
    canonical_claimed_payload,
    payload_binding_digest,
    source_task_payload,
)
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
        "agent_session_id": "synthetic-session",
        "execution_attempt": 1,
        "task_payload_digest": payload_binding_digest({"model": "auto/code"}),
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

    now_ms = int(time.time() * 1000)
    row = {
        "task": {
            "status": "CLAIMED",
            "claim_id": "claim-1",
            "claimed_by": "hermes-test",
            "agent_session_id": "synthetic-session",
            "execution_attempt": 1,
            "lease_expires_at_ts": now_ms + 120_000,
            "payload_json": '{"model":"auto/code"}',
        },
        "projection_generation": 7,
        "projection_status": "approved",
        "projection_expires_at_ts": now_ms + 120_000,
    }
    app = ExecutorAuthMiddleware(
        downstream, neo_factory=lambda: _SyntheticExecutorClaimsDB(row),
    )
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
        "agent_session_id": "synthetic-session",
        "execution_attempt": 1,
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


def test_payload_digest_is_canonical_and_domain_separated():
    payload, frozen = canonical_claimed_payload('{"z":[1,2],"a":{"x":true}}')
    assert frozen == b'{"a":{"x":true},"z":[1,2]}'
    digest = payload_binding_digest(payload)
    assert digest.startswith("sha256:") and len(digest) == 71
    assert digest == payload_binding_digest('{"a":{"x":true},"z":[1,2]}')
    assert digest != payload_binding_digest({"a": {"x": False}, "z": [1, 2]})
    assert source_task_payload({
        "payload_json": '{"a":1}',
        "payload": {"a": 2},
    })[0] == {"a": 1}


@pytest.mark.parametrize("raw", [
    '{"a":1,"a":2}', '{"x":NaN}', '{"x":Infinity}', '[1,2]',
    'null', '{"x":', "", {"x": float("nan")}, {1: "not JSON key"},
])
def test_canonical_binding_rejects_ambiguous_or_unserializable_source(raw):
    with pytest.raises(ClaimBindingError):
        canonical_claimed_payload(raw)


def test_issuer_signs_exact_canonical_claim_and_session(monkeypatch):
    response, signed = _issue_synthetic_task_token(
        monkeypatch, '{"allowed_tools":[],"model":"approved/model"}',
    )
    assert response.status_code == 200
    claim = signed[0]
    assert claim["task_payload_digest"] == payload_binding_digest(
        {"model": "approved/model", "allowed_tools": []},
    )
    assert claim["agent_session_id"] == "synthetic-session"
    assert claim["execution_attempt"] == 1
    assert claim["projection_generation"] == 7


def _source_binding_row():
    now_ms = int(time.time() * 1000)
    return {
        "task": {
            "status": "CLAIMED",
            "claim_id": "claim-1",
            "claimed_by": "hermes-test",
            "agent_session_id": "synthetic-session",
            "execution_attempt": 1,
            "lease_expires_at_ts": now_ms + 120_000,
            "payload_json": '{"model":"auto/code"}',
        },
        "projection_generation": 7,
        "projection_status": "approved",
        "projection_expires_at_ts": now_ms + 180_000,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    ("payload_json", '{"model":"another/model"}'),
    ("payload_json", '{"model":"a","model":"b"}'),
    ("payload_json", None),
    ("status", "CANCELLED"),
    ("claim_id", "restored-old-claim"),
    ("claimed_by", "different-worker"),
    ("agent_session_id", "older-session"),
    ("execution_attempt", 2),
    ("lease_expires_at_ts", 0),
])
async def test_source_changes_deny_at_api_boundary_before_downstream(
    monkeypatch, mutation,
):
    private, public_pem = _keys()
    monkeypatch.setenv("ASSISTX_EXECUTOR_VERIFY_KEY_PEM", public_pem)
    monkeypatch.setenv("ASSISTX_EXECUTOR_KEY_ID", "test-key")
    token = ExecutorTokenCodec(private_key=private, key_id="test-key").encode(_claims())
    row = _source_binding_row()
    row["task"][mutation[0]] = mutation[1]
    observed = []

    async def downstream(scope, receive, send):
        observed.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = ExecutorAuthMiddleware(
        downstream, neo_factory=lambda: _SyntheticExecutorClaimsDB(row),
    )
    sent = await _invoke(
        app, method="POST", path="/api/tasks/task-1/heartbeat",
        headers=[(b"authorization", f"Bearer {token}".encode())],
        body=b"{}",
    )
    assert sent[0]["status"] == 401
    assert not observed


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("projection_generation", 6),
    ("projection_status", "revoked"),
    ("projection_expires_at_ts", 0),
])
async def test_projection_replacement_or_expiry_denied_before_api_effect(
    monkeypatch, field, value,
):
    private, public_pem = _keys()
    monkeypatch.setenv("ASSISTX_EXECUTOR_VERIFY_KEY_PEM", public_pem)
    monkeypatch.setenv("ASSISTX_EXECUTOR_KEY_ID", "test-key")
    token = ExecutorTokenCodec(private_key=private, key_id="test-key").encode(_claims())
    row = _source_binding_row()
    row[field] = value

    async def must_not_dispatch(*_args):
        pytest.fail("resource API effect was admitted")

    app = ExecutorAuthMiddleware(
        must_not_dispatch, neo_factory=lambda: _SyntheticExecutorClaimsDB(row),
    )
    sent = await _invoke(
        app, method="POST", path="/api/tasks/task-1/complete",
        headers=[(b"authorization", f"Bearer {token}".encode())],
        body=b"{}",
    )
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_missing_or_unavailable_resource_verifier_denies_executor_token(monkeypatch):
    private, public_pem = _keys()
    monkeypatch.setenv("ASSISTX_EXECUTOR_VERIFY_KEY_PEM", public_pem)
    monkeypatch.setenv("ASSISTX_EXECUTOR_KEY_ID", "test-key")
    token = ExecutorTokenCodec(private_key=private, key_id="test-key").encode(_claims())

    async def must_not_dispatch(*_args):
        pytest.fail("resource API effect was admitted")

    headers = [(b"authorization", f"Bearer {token}".encode())]
    missing = await _invoke(
        ExecutorAuthMiddleware(must_not_dispatch),
        method="POST", path="/api/tasks/task-1/heartbeat", headers=headers,
    )
    assert missing[0]["status"] == 401

    def unreachable_db():
        raise OSError("synthetic DB unavailable")

    unavailable = await _invoke(
        ExecutorAuthMiddleware(must_not_dispatch, neo_factory=unreachable_db),
        method="POST", path="/api/tasks/task-1/heartbeat", headers=headers,
    )
    assert unavailable[0]["status"] == 503


@pytest.mark.asyncio
async def test_valid_current_source_allows_context_request(monkeypatch):
    private, public_pem = _keys()
    monkeypatch.setenv("ASSISTX_EXECUTOR_VERIFY_KEY_PEM", public_pem)
    monkeypatch.setenv("ASSISTX_EXECUTOR_KEY_ID", "test-key")
    token = ExecutorTokenCodec(private_key=private, key_id="test-key").encode(_claims())
    seen = []

    async def downstream(scope, receive, send):
        seen.append(dict(scope["headers"]))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = ExecutorAuthMiddleware(
        downstream, neo_factory=lambda: _SyntheticExecutorClaimsDB(_source_binding_row()),
    )
    sent = await _invoke(
        app, method="POST", path="/api/brain/context",
        headers=[(b"authorization", f"Bearer {token}".encode())],
        body=b'{"task_id":"task-1"}',
    )
    assert sent[0]["status"] == 204
    assert seen[0][b"x-assistx-executor-identity"] == b"executor:hermes-test:claim-1"


@pytest.mark.asyncio
async def test_signed_claim_with_missing_binding_is_rejected(monkeypatch):
    private, public_pem = _keys()
    monkeypatch.setenv("ASSISTX_EXECUTOR_VERIFY_KEY_PEM", public_pem)
    monkeypatch.setenv("ASSISTX_EXECUTOR_KEY_ID", "test-key")
    claim = _claims()
    claim.pop("task_payload_digest")
    token = ExecutorTokenCodec(private_key=private, key_id="test-key").encode(claim)

    async def forbidden(*_args):
        pytest.fail("old unbound token admitted")

    sent = await _invoke(
        ExecutorAuthMiddleware(
            forbidden, neo_factory=lambda: _SyntheticExecutorClaimsDB(_source_binding_row()),
        ),
        method="POST", path="/api/tasks/task-1/heartbeat",
        headers=[(b"authorization", f"Bearer {token}".encode())],
    )
    assert sent[0]["status"] == 401
