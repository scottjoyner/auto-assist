"""Hermetic cross-service current-claim status contract.

Synthetic row snapshots only; no fleet, key material, Docker or live Neo4j.
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistx.claim_binding import payload_binding_digest
from assistx.executor_security import build_executor_security_router


class FakeTaskDB:
    def __init__(self, row):
        self.row = row

    def _session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, *_args, **_kwargs):
        return self

    def single(self):
        return self.row

    def close(self):
        return None


def _row():
    now_ms = int(time.time() * 1000)
    return {
        "task": {
            "id": "task-1",
            "status": "RUNNING",
            "claim_id": "claim-1",
            "claimed_by": "hermes-test",
            "agent_session_id": "session-1",
            "execution_attempt": 3,
            "lease_expires_at_ts": now_ms + 120_000,
            "payload_json": '{"model":"liquid/lfm3","allowed_tools":[]}',
        },
        "projection_generation": 7,
        "projection_status": "approved",
        "projection_expires_at_ts": now_ms + 120_000,
    }


def _status(monkeypatch, row=None, *, authorized=True):
    monkeypatch.setenv("ASSISTX_EXECUTOR_CLAIM_STATUS_TOKEN", "test-status-only")
    monkeypatch.setenv("ASSISTX_REQUIRE_OWNER_BINDING", "false")
    app = FastAPI()
    app.include_router(
        build_executor_security_router(lambda: FakeTaskDB(row if row is not None else _row()))
    )
    return TestClient(app).get(
        "/api/executor/claims/task-1/status",
        headers={
            "Authorization": "Bearer test-status-only" if authorized else "Bearer wrong"
        },
    )


def test_status_uses_canonical_signed_payload_and_current_claim(monkeypatch):
    res = _status(monkeypatch)
    assert res.status_code == 200
    result = res.json()
    assert result["active"] is True
    assert result["claim_id"] == "claim-1"
    assert result["agent_id"] == "hermes-test"
    assert result["agent_session_id"] == "session-1"
    assert result["execution_attempt"] == 3
    assert result["projection_generation"] == 7
    assert result["task_payload_digest"] == payload_binding_digest(
        {"model": "liquid/lfm3", "allowed_tools": []},
    )


@pytest.mark.parametrize("field,value,reason", [
    ("status", "PAUSING", "task_not_active"),
    ("status", "CANCELLED", "task_not_active"),
    ("claim_id", "", "claim_identity_missing"),
    ("claimed_by", None, "claim_identity_missing"),
    ("agent_session_id", "", "claim_revision_missing"),
    ("execution_attempt", 0, "claim_revision_missing"),
    ("execution_attempt", True, "claim_revision_missing"),
    ("execution_attempt", "3", "claim_revision_missing"),
    ("lease_expires_at_ts", 0, "claim_expired"),
    ("lease_expires_at_ts", True, "claim_expired"),
    ("payload_json", '{"x":', "canonical_payload_invalid"),
    ("payload_json", '{"a":1,"a":2}', "canonical_payload_invalid"),
    ("payload_json", '{"x":NaN}', "canonical_payload_invalid"),
    ("payload_json", None, "canonical_payload_invalid"),
    ("payload_json", "[]", "canonical_payload_invalid"),
])
def test_claim_status_never_claims_active_for_invalid_task(
    monkeypatch, field, value, reason,
):
    row = _row()
    row["task"][field] = value
    if field == "payload_json":
        row["task"]["payload"] = {"model": "unsafe-legacy-fallback"}
    res = _status(monkeypatch, row)
    assert res.status_code == 200
    assert res.json()["active"] is False
    assert res.json()["reason"] == reason


@pytest.mark.parametrize("field,value,reason", [
    ("projection_generation", 0, "projection_not_approved"),
    ("projection_generation", True, "projection_not_approved"),
    ("projection_status", "revoked", "projection_not_approved"),
    ("projection_expires_at_ts", 0, "projection_expired"),
])
def test_claim_status_denies_unapproved_or_noncanonical_projection(
    monkeypatch, field, value, reason,
):
    row = _row()
    row[field] = value
    res = _status(monkeypatch, row)
    assert res.status_code == 200
    assert res.json()["active"] is False
    assert res.json()["reason"] == reason


def test_status_authentication_is_independent_of_executor_bootstrap(monkeypatch):
    response = _status(monkeypatch, authorized=False)
    assert response.status_code == 401


def test_status_missing_source_task_denies(monkeypatch):
    response = _status(monkeypatch, row={})
    assert response.status_code == 200
    assert response.json()["active"] is False
    assert response.json()["reason"] == "not_found"
