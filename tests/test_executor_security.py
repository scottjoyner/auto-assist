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
