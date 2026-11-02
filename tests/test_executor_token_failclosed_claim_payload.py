"""Synthetic-only token issuance security tests; no Neo4j or provider calls."""
from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import assistx.executor_security as security


class _FakeSession:
    def __init__(self, task):
        self.task = task

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def run(self, *_args, **_kwargs):
        now = int(time.time() * 1000)
        row = {
            "task": self.task,
            "projection_generation": 4,
            "projection_status": "approved",
            "projection_expires_at_ts": now + 120_000,
        }
        return type("Result", (), {"single": lambda self: row})()


class _FakeNeo:
    def __init__(self, task):
        self.task = task

    def _session(self):
        return _FakeSession(self.task)

    def close(self):
        pass


class _FakeSigner:
    def __init__(self):
        self.calls = []

    def encode(self, claims):
        self.calls.append(claims)
        return "synthetic.not.a.jwt"


@pytest.fixture
def issuance(monkeypatch):
    signer = _FakeSigner()
    monkeypatch.setenv("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN", "synthetic-bootstrap")
    monkeypatch.setenv("ASSISTX_REQUIRE_OWNER_BINDING", "false")
    monkeypatch.delenv("ASSISTX_EXECUTOR_DEFAULT_MODEL_ALIASES", raising=False)
    monkeypatch.setattr(
        security.ExecutorTokenCodec, "signer_from_env", classmethod(lambda cls: signer),
    )

    def send(payload, *, field="payload_json", model_aliases=None):
        now = int(time.time() * 1000)
        task = {
            "id": "t-1", "claim_id": "c-1", "claimed_by": "a-1",
            "status": "CLAIMED", "lease_expires_at_ts": now + 120_000,
            field: payload,
        }
        app = FastAPI()
        app.include_router(security.build_executor_security_router(lambda: _FakeNeo(task)))
        response = TestClient(app).post(
            "/api/executor/claims/t-1/token",
            headers={"Authorization": "Bearer synthetic-bootstrap"},
            json={"agent_id": "a-1", "claim_id": "c-1"},
        )
        return response

    return send, signer


@pytest.mark.parametrize("payload", [
    "{bad", "[]", "null", "", "{\"model\":\"ok\",\"model\":\"..\"}",
    "{\"model\":NaN}", "{\"model\":Infinity}",
])
def test_invalid_claimed_payload_denied_before_signer(issuance, payload):
    send, signer = issuance
    assert send(payload).status_code == 409
    assert signer.calls == []


@pytest.mark.parametrize("payload", [
    {"model": ".."}, {"model": "."}, {"model": "../../outside"},
    {"model": "/absolute"}, {"model": "valid\\bad"},
    {"allowed_model_aliases": ["safe", ".."]},
    {"allowed_model_aliases": []},
    {"model": "ok", "allowed_tools": [123]},
    {"model": "ok", "allowed_tools": "unknown"},
    {"model": "ok", "allowed_tools": {"file": True}},
])
def test_invalid_model_or_tools_never_sign(issuance, payload):
    send, signer = issuance
    assert send(json.dumps(payload)).status_code == 409
    assert signer.calls == []


def test_explicit_empty_tools_are_not_widened(issuance):
    send, signer = issuance
    for empty in ([], ""):
        response = send(json.dumps({"model": "auto/code", "allowed_tools": empty}))
        assert response.status_code == 200
        assert response.json()["claims"]["allowed_tools"] == []
    assert len(signer.calls) == 2


def test_absent_tools_retain_documented_legacy_policy(issuance):
    send, signer = issuance
    response = send(json.dumps({"model": "auto/code"}))
    assert response.status_code == 200
    assert "file" in response.json()["claims"]["allowed_tools"]
    assert len(signer.calls) == 1


def test_exact_valid_scopes_and_no_unsupported_permissions(issuance):
    send, signer = issuance
    response = send(json.dumps({
        "model": "local/reasoning-large",
        "allowed_tools": ["memory", "file"],
    }))
    assert response.status_code == 200
    assert response.json()["claims"]["allowed_tools"] == ["file", "memory"]
    assert response.json()["claims"]["allowed_model_aliases"] == ["local/reasoning-large"]
    assert len(signer.calls) == 1


def test_legacy_structured_claim_payload_supported(issuance):
    send, signer = issuance
    response = send({"model": "auto/code", "allowed_tools": []}, field="payload")
    assert response.status_code == 200
    assert signer.calls[0]["allowed_tools"] == []
