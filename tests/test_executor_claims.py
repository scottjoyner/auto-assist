from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistx.executor_claims import (
    assert_claim_matches,
    build_executor_claim_router,
    read_claim_state,
)
from assistx.executor_security import ExecutorTokenError


class _Result:
    def __init__(self, row):
        self.row = row

    def single(self):
        return self.row


class _Session:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def run(self, _query, _params=None):
        return _Result(self.row)


class _Neo:
    def __init__(self, row):
        self.row = row
        self.closed = False

    def _session(self):
        return _Session(self.row)

    def close(self):
        self.closed = True


def _active_row(**overrides):
    now_ms = int(time.time() * 1000)
    row = {
        "status": "RUNNING",
        "agent_id": "hermes-test",
        "claim_id": "claim-1",
        "lease_expires_at_ts": now_ms + 120_000,
        "execution_attempt": 1,
        "projection_generation": 8,
        "projection_status": "approved",
        "projection_expires_at_ts": now_ms + 120_000,
    }
    row.update(overrides)
    return row


def _claims(**overrides):
    claims = {
        "task_id": "task-1",
        "claim_id": "claim-1",
        "agent_id": "hermes-test",
        "projection_generation": 8,
        "exp": int(time.time()) + 60,
    }
    claims.update(overrides)
    return claims


def test_read_claim_state_uses_authoritative_claim_fields():
    neo = _Neo(_active_row())
    state = read_claim_state(lambda: neo, "task-1")
    assert state["active"] is True
    assert state["claim_id"] == "claim-1"
    assert state["agent_id"] == "hermes-test"
    assert state["projection_generation"] == 8
    assert neo.closed is True


def test_claim_match_rejects_revocation_generation_change_and_token_overrun():
    state = {**_active_row(), "active": True, "task_id": "task-1"}
    assert_claim_matches(_claims(), state)

    with pytest.raises(ExecutorTokenError, match="no longer matches"):
        assert_claim_matches(_claims(claim_id="old-claim"), state)

    with pytest.raises(ExecutorTokenError, match="no longer matches"):
        assert_claim_matches(_claims(projection_generation=7), state)

    with pytest.raises(ExecutorTokenError, match="outlives"):
        assert_claim_matches(
            _claims(exp=int(time.time()) + 300),
            state,
        )


def test_claim_status_endpoint_requires_router_service_token(monkeypatch):
    monkeypatch.setenv("ASSISTX_EXECUTOR_SERVICE_TOKEN", "router-only-secret")
    app = FastAPI()
    app.include_router(build_executor_claim_router(lambda: _Neo(_active_row())))
    client = TestClient(app)

    denied = client.get("/api/executor/claims/task-1/status")
    assert denied.status_code == 401

    response = client.get(
        "/api/executor/claims/task-1/status",
        headers={"Authorization": "Bearer router-only-secret"},
    )
    assert response.status_code == 200
    assert response.json()["active"] is True
