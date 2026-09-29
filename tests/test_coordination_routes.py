"""Tests for the fleet coordination guardrail."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistx.coordination_routes import CoordinationStore, build_coordination_router


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    path = tmp_path / "FLEET-COORDINATION.json"
    app.include_router(build_coordination_router(path=str(path)))
    return TestClient(app), path


def test_claim_and_snapshot_roundtrip(client):
    tc, path = client
    r = tc.post("/api/coordination/claim", json={"op": "benchmark", "owner": "agent-a", "ttl_minutes": 30, "note": "soak"})
    assert r.status_code == 200
    assert r.json()["claimed"] is True

    snap = tc.get("/api/coordination").json()
    assert snap["exclusive_ops"]["benchmark"]["owner"] == "agent-a"
    assert json.loads(path.read_text())["exclusive_ops"]["benchmark"]["note"] == "soak"


def test_conflicting_claim_rejected_with_409(client):
    tc, _ = client
    tc.post("/api/coordination/claim", json={"op": "lmstudio-restart", "owner": "agent-a"})
    r = tc.post("/api/coordination/claim", json={"op": "lmstudio-restart", "owner": "agent-b"})
    assert r.status_code == 409


def test_same_owner_can_refresh_claim(client):
    tc, _ = client
    tc.post("/api/coordination/claim", json={"op": "benchmark", "owner": "agent-a", "ttl_minutes": 5})
    r = tc.post("/api/coordination/claim", json={"op": "benchmark", "owner": "agent-a", "ttl_minutes": 60})
    assert r.status_code == 200
    assert r.json()["claim"]["ttl_minutes" if False else "expires_at"]


def test_expired_claims_auto_release(client):
    tc, path = client
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    path.write_text(json.dumps({"exclusive_ops": {"benchmark": {"owner": "ghost", "expires_at": expired}}}))

    snap = tc.get("/api/coordination").json()
    assert snap["exclusive_ops"] == {}


def test_release_requires_owner(client):
    tc, _ = client
    tc.post("/api/coordination/claim", json={"op": "router-restart", "owner": "agent-a"})
    r = tc.post("/api/coordination/release", json={"op": "router-restart", "owner": "agent-b"})
    assert r.status_code == 403
    r = tc.post("/api/coordination/release", json={"op": "router-restart", "owner": "agent-a"})
    assert r.json()["released"] is True


def test_store_survives_corrupt_file(tmp_path):
    p = tmp_path / "coord.json"
    p.write_text("{not json")
    store = CoordinationStore(path=str(p))
    assert store.snapshot()["exclusive_ops"] == {}
    r = store.claim("op", "owner")
    assert r["claimed"] is True
