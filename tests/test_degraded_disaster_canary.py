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
from assistx.degraded_control_hardening import install_degraded_control_hardening
from assistx.degraded_control_plane import (
    DegradedControlPlaneRuntime,
    build_degraded_control_router,
    install_degraded_route_fence,
)
from assistx.degraded_router_gate import (
    install_degraded_router_activation_requirements,
)
from assistx.operational_journal import AppendOnlyOperationJournal
from assistx.operational_state import OperationalRecord
from assistx.recovery_island import sign_recovery_activation
from assistx.runtime_projection import projection_checksum, projection_signature

PROJECTION_SECRET = "projection-secret"
ACTIVATION_SECRET = "activation-secret"
BUNDLE_SHA = "c" * 64

install_degraded_control_hardening()
install_degraded_router_activation_requirements()


class MemoryStore:
    graph = "assistx_operational"

    def __init__(self):
        self.records = {}

    def get(self, kind, logical_id):
        record = self.records.get((kind, logical_id))
        if record and record.expires_at_ms > int(time.time() * 1000):
            return record
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


class Session:
    def __init__(self, commits):
        self.commits = commits

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def run(self, _query, parameters):
        self.commits.append(parameters)

        class Result:
            @staticmethod
            def single():
                return {
                    "commit_id": parameters["commit_id"],
                    "created_at_ts": int(time.time() * 1000),
                }

        return Result()


class Neo:
    def __init__(self, commits):
        self.commits = commits

    def _session(self):
        return Session(self.commits)

    def close(self):
        return None


def projection():
    now = int(time.time() * 1000)
    document = {
        "schema_version": "1",
        "source": "assistx",
        "generation": 12,
        "revision": "fleet-12",
        "generated_at_ms": now,
        "expires_at_ms": now + 300_000,
        "providers": [
            {
                "name": "assistx-xwing-lmstudio",
                "type": "lmstudio",
                "node_id": "xwing",
                "runtime_instance_id": "lmstudio-xwing-1234",
                "parallel_slots": 2,
                "enabled": True,
                "base_url": "http://192.168.1.9:1234/v1",
                "access_urls": [
                    "http://192.168.1.9:1234/v1",
                    "http://100.64.0.9:1234/v1",
                ],
                "quota_class": "local",
                "models": [
                    {
                        "alias": "auto/code",
                        "provider_model": "qwen.gguf",
                        "capabilities": ["chat", "code", "local_only"],
                    }
                ],
            }
        ],
    }
    document["checksum"] = projection_checksum(document)
    document["signature"] = projection_signature(
        document["generation"],
        document["checksum"],
        document["generated_at_ms"],
        document["expires_at_ms"],
        PROJECTION_SECRET,
    )
    return document


def activation():
    return sign_recovery_activation(
        {
            "target_node_id": "beelink-recovery",
            "deployment": "assistx-degraded",
            "bundle_sha256": BUNDLE_SHA,
            "epoch": 20,
            "fence_proof": "witness:disaster-canary-exclusive",
        },
        key_id="activation-v1",
        secret=ACTIVATION_SECRET,
        ttl_seconds=600,
    )


def test_total_primary_failure_and_clean_return(tmp_path, monkeypatch):
    commits = []
    value = DegradedControlPlaneRuntime(
        MemoryStore(),
        AppendOnlyOperationJournal(tmp_path / "journal.jsonl"),
        neo_factory=lambda: Neo(commits),
    )
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
    monkeypatch.setenv("ASSISTX_RUNTIME_PROJECTION_HMAC_SECRET", PROJECTION_SECRET)
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
    monkeypatch.delenv("NEO4J_URI", raising=False)
    client = TestClient(app)

    # Warm standby accepts only the signed snapshot and remains zero-capacity.
    assert client.post(
        "/api/degraded/runtime-projection/publish",
        json=projection(),
    ).status_code == 200
    assert client.get("/api/degraded/runtime-projection").status_code == 423
    assert client.post(
        "/api/degraded/claims",
        json={"logical_id": "task-1", "owner": "beelink", "epoch": 20},
    ).status_code == 423

    # An independently fenced activation opens the bounded coordination surface.
    activated = client.post(
        "/api/degraded/activate",
        json={"activation": activation()},
    )
    assert activated.status_code == 200
    assert client.get("/api/degraded/runtime-projection").status_code == 200

    # A surviving LM Studio node must prove current liveness before delegation.
    assert client.post(
        "/api/degraded/heartbeats",
        json={
            "node_id": "xwing",
            "inflight": 0,
            "capabilities": ["code"],
            "ttl_seconds": 45,
        },
    ).status_code == 200
    planned = client.post(
        "/api/degraded/delegations/plan",
        json={
            "task_id": "task-1",
            "owner": "beelink",
            "epoch": 20,
            "required_capabilities": ["code"],
        },
    )
    assert planned.status_code == 200
    assert planned.json()["payload"]["target_node_id"] == "xwing"

    # With Neo4j gone, the result is durable-journaled but never called complete.
    pending = client.post(
        "/api/degraded/finalizations",
        json={
            "operation_id": "task-1",
            "operation_kind": "task_outcome",
            "final_state": "COMPLETED",
            "record_checksum": "d" * 64,
            "epoch": 20,
            "evidence": {"artifact": "sha256:result"},
        },
    )
    assert pending.status_code == 200
    assert pending.json()["status"] == "PENDING_DURABLE_COMMIT"
    assert value.journal.verify()["pending"] == 1

    # Emergency pressure blocks promotion before the host guard sheds the model.
    pressure = client.post(
        "/api/degraded/memory-pressure/evaluate",
        json={"available_mb": 700, "total_mb": 14336},
    )
    assert pressure.json()["state"] == "EMERGENCY"

    # When Neo4j returns, replay commits exactly once and leadership is relinquished.
    handoff = client.post(
        "/api/degraded/primary-return/reconcile",
        json={"owner": "witness:disaster-canary-exclusive", "epoch": 20},
    )
    assert handoff.status_code == 200
    assert handoff.json()["status"] == "RELINQUISHED"
    assert handoff.json()["replay"]["remaining"] == 0
    assert len(commits) == 1
    assert value.journal.verify()["pending"] == 0
