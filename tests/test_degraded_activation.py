from __future__ import annotations

import hashlib
import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistx.degraded_activation import (
    build_degraded_activation_router,
    install_degraded_activation_fence,
)
from assistx.degraded_control_plane import (
    DegradedControlPlaneRuntime,
    build_degraded_control_router,
    install_degraded_route_fence,
)
from assistx.operational_journal import AppendOnlyOperationJournal
from assistx.operational_state import OperationalRecord
from assistx.recovery_island import sign_recovery_activation

ACTIVATION_SECRET = "activation-secret"
BUNDLE_SHA = "b" * 64


class MemoryStore:
    graph = "assistx_operational"

    def __init__(self):
        self.records = {}

    def get(self, kind, logical_id):
        value = self.records.get((kind, logical_id))
        if value and value.expires_at_ms > int(time.time() * 1000):
            return value
        return None

    def upsert(self, *, kind, logical_id, state, payload=None, owner=None, epoch=0, ttl_seconds=300):
        return self._write(kind, logical_id, state, payload, owner, epoch, ttl_seconds)

    def upsert_fenced(self, *, kind, logical_id, state, owner, epoch, payload=None, ttl_seconds=300):
        current = self.get(kind, logical_id)
        if current and current.owner != owner:
            raise RuntimeError("fenced record is held by another owner")
        if current and current.epoch > int(epoch):
            raise RuntimeError("fenced record epoch moved backwards")
        return self._write(kind, logical_id, state, payload, owner, epoch, ttl_seconds)

    def _write(self, kind, logical_id, state, payload, owner, epoch, ttl_seconds):
        now = int(time.time() * 1000)
        record = OperationalRecord(
            record_id=f"{kind}:{logical_id}",
            kind=kind,
            logical_id=logical_id,
            state=str(state).upper(),
            owner=owner,
            epoch=int(epoch),
            payload=dict(payload or {}),
            created_at_ms=now,
            updated_at_ms=now,
            expires_at_ms=now + int(ttl_seconds) * 1000,
            checksum=hashlib.sha256(json.dumps(payload or {}, sort_keys=True).encode()).hexdigest(),
        )
        self.records[(kind, logical_id)] = record
        return record


def runtime(tmp_path):
    return DegradedControlPlaneRuntime(
        MemoryStore(),
        AppendOnlyOperationJournal(tmp_path / "journal.jsonl"),
    )


def activation():
    return sign_recovery_activation(
        {
            "target_node_id": "beelink-recovery",
            "deployment": "assistx-degraded",
            "bundle_sha256": BUNDLE_SHA,
            "epoch": 1,
            "fence_proof": "witness:test-exclusive-lease",
        },
        key_id="activation-v1",
        secret=ACTIVATION_SECRET,
        ttl_seconds=300,
    )


def test_warm_degraded_stack_rejects_claims_until_signed_activation(
    tmp_path,
    monkeypatch,
):
    value = runtime(tmp_path)
    app = FastAPI()
    app.include_router(
        build_degraded_control_router(
            lambda: "operator",
            runtime_factory=lambda: value,
        )
    )
    app.include_router(
        build_degraded_activation_router(
            lambda: "operator",
            runtime_factory=lambda: value,
        )
    )
    install_degraded_route_fence(app)
    install_degraded_activation_fence(app, lambda: value)

    monkeypatch.setenv("ASSISTX_DEGRADED_CONTROL_PLANE", "true")
    monkeypatch.setenv(
        "ASSISTX_RECOVERY_ACTIVATION_VERIFY_KEYS",
        json.dumps({"activation-v1": ACTIVATION_SECRET}),
    )
    monkeypatch.setenv("ASSISTX_RECOVERY_BUNDLE_SHA256", BUNDLE_SHA)
    monkeypatch.setenv("FLEET_NODE_ID", "beelink-recovery")
    monkeypatch.setenv("ASSISTX_DEGRADED_DEPLOYMENT_NAME", "assistx-degraded")
    monkeypatch.setenv(
        "ASSISTX_DEGRADED_ACTIVATION_NONCE_DIR",
        str(tmp_path / "nonces"),
    )
    client = TestClient(app)

    claim = {
        "logical_id": "task-1",
        "owner": "beelink-recovery",
        "epoch": 1,
        "ttl_seconds": 60,
    }
    assert client.post("/api/degraded/claims", json=claim).status_code == 423

    activated = client.post(
        "/api/degraded/activate",
        json={"activation": activation()},
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "DEGRADED_ACTIVE"
    assert client.post("/api/degraded/claims", json=claim).status_code == 200


def test_activation_replay_is_rejected(tmp_path, monkeypatch):
    value = runtime(tmp_path)
    app = FastAPI()
    app.include_router(
        build_degraded_activation_router(
            lambda: "operator",
            runtime_factory=lambda: value,
        )
    )
    install_degraded_route_fence(app)
    monkeypatch.setenv("ASSISTX_DEGRADED_CONTROL_PLANE", "true")
    monkeypatch.setenv(
        "ASSISTX_RECOVERY_ACTIVATION_VERIFY_KEYS",
        json.dumps({"activation-v1": ACTIVATION_SECRET}),
    )
    monkeypatch.setenv("ASSISTX_RECOVERY_BUNDLE_SHA256", BUNDLE_SHA)
    monkeypatch.setenv("FLEET_NODE_ID", "beelink-recovery")
    monkeypatch.setenv("ASSISTX_DEGRADED_DEPLOYMENT_NAME", "assistx-degraded")
    monkeypatch.setenv(
        "ASSISTX_DEGRADED_ACTIVATION_NONCE_DIR",
        str(tmp_path / "nonces"),
    )
    client = TestClient(app)
    envelope = activation()

    assert client.post("/api/degraded/activate", json={"activation": envelope}).status_code == 200
    replay = client.post("/api/degraded/activate", json={"activation": envelope})
    assert replay.status_code == 409
    assert any(
        marker in replay.json()["detail"]
        for marker in ("replay", "stale_recovery_activation_epoch")
    )
