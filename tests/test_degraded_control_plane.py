from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistx.degraded_control_plane import (
    DegradedControlPlaneRuntime,
    build_degraded_control_router,
    install_degraded_route_fence,
)
from assistx.operational_journal import AppendOnlyOperationJournal
from assistx.operational_state import OperationalRecord
from assistx.runtime_projection import projection_checksum, projection_signature

SECRET = "projection-secret"
NOW = 1_000_000


class MemoryStore:
    graph = "assistx_operational"

    def __init__(self, clock_ms=lambda: NOW):
        self.clock_ms = clock_ms
        self.records = {}

    def upsert(
        self,
        *,
        kind,
        logical_id,
        state,
        payload=None,
        owner=None,
        epoch=0,
        ttl_seconds=300,
    ):
        now = self.clock_ms()
        previous = self.records.get((kind, logical_id))
        value = OperationalRecord(
            record_id=f"{kind}:{logical_id}",
            kind=kind,
            logical_id=logical_id,
            state=str(state).upper(),
            owner=owner,
            epoch=int(epoch),
            payload=dict(payload or {}),
            created_at_ms=previous.created_at_ms if previous else now,
            updated_at_ms=now,
            expires_at_ms=now + int(ttl_seconds) * 1000,
            checksum=hashlib.sha256(
                json.dumps(payload or {}, sort_keys=True).encode()
            ).hexdigest(),
        )
        self.records[(kind, logical_id)] = value
        return value

    def upsert_fenced(self, *, kind, logical_id, state, owner, epoch, **kwargs):
        current = self.get(kind, logical_id)
        if current and current.owner != owner:
            raise RuntimeError("fenced record is held by another owner")
        if current and current.epoch > int(epoch):
            raise RuntimeError("fenced record epoch moved backwards")
        return self.upsert(
            kind=kind,
            logical_id=logical_id,
            state=state,
            owner=owner,
            epoch=epoch,
            **kwargs,
        )

    def get(self, kind, logical_id):
        value = self.records.get((kind, logical_id))
        if value and value.expires_at_ms > self.clock_ms():
            return value
        return None


class FakeResult:
    def __init__(self, row):
        self.row = row

    def single(self):
        return self.row


class FakeSession:
    def __init__(self, commits):
        self.commits = commits

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def run(self, _query, parameters):
        self.commits.append(parameters)
        return FakeResult(
            {
                "commit_id": parameters["commit_id"],
                "created_at_ts": NOW,
            }
        )


class FakeNeo:
    def __init__(self, commits):
        self.commits = commits

    def _session(self):
        return FakeSession(self.commits)

    def close(self):
        return None


def runtime(tmp_path, *, neo_factory=None):
    store = MemoryStore()
    journal = AppendOnlyOperationJournal(
        tmp_path / "journal.jsonl",
        clock_ms=lambda: NOW,
    )
    return DegradedControlPlaneRuntime(
        store,
        journal,
        neo_factory=neo_factory,
        clock_ms=lambda: NOW,
    )


def signed_projection():
    document = {
        "schema_version": "1",
        "source": "assistx",
        "generation": 9,
        "revision": "fleet-9",
        "generated_at_ms": NOW,
        "expires_at_ms": NOW + 60_000,
        "providers": [
            {
                "name": "assistx-xwing-lmstudio",
                "type": "lmstudio",
                "node_id": "xwing",
                "runtime_instance_id": "lmstudio-xwing-1234",
                "runtime_kind": "lmstudio",
                "runtime_version": "0.4.7",
                "parallel_slots": 2,
                "queue_limit": 4,
                "queue_timeout_seconds": 30,
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
                        "model_instance_id": "model-xwing-1",
                        "artifact_fingerprint": "sha256:abc",
                        "quantization": "Q4_K_M",
                        "capabilities": ["chat", "code", "tool_use", "local_only"],
                        "context_window": 32768,
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
        SECRET,
    )
    return document


def test_signed_projection_is_cached_and_restored_from_journal(tmp_path):
    value = runtime(tmp_path)
    projection = signed_projection()
    value.publish_runtime_projection(projection, secret=SECRET)
    assert value.get_runtime_projection()["generation"] == 9

    value.store.records.clear()
    restored = value.get_runtime_projection()
    assert restored["checksum"] == projection["checksum"]


def test_projection_tampering_and_public_provider_are_rejected(tmp_path):
    value = runtime(tmp_path)
    tampered = signed_projection()
    tampered["providers"][0]["parallel_slots"] = 99
    with pytest.raises(ValueError, match="checksum mismatch"):
        value.publish_runtime_projection(tampered, secret=SECRET)

    public = signed_projection()
    public["providers"][0]["quota_class"] = "hosted"
    public["checksum"] = projection_checksum(public)
    public["signature"] = projection_signature(
        public["generation"],
        public["checksum"],
        public["generated_at_ms"],
        public["expires_at_ms"],
        SECRET,
    )
    with pytest.raises(ValueError, match="local providers only"):
        value.publish_runtime_projection(public, secret=SECRET)


def test_delegation_uses_signed_projection_and_live_headroom(tmp_path):
    value = runtime(tmp_path)
    value.publish_runtime_projection(signed_projection(), secret=SECRET)
    value.record_heartbeat(
        {
            "node_id": "xwing",
            "capabilities": ["code"],
            "inflight": 1,
            "ttl_seconds": 45,
        }
    )

    planned = value.plan_delegation(
        {
            "task_id": "task-1",
            "owner": "beelink",
            "epoch": 4,
            "required_capabilities": ["code"],
        }
    )
    assert planned["state"] == "PLANNED"
    assert planned["payload"]["target_node_id"] == "xwing"
    assert planned["payload"]["headroom"] == 1
    assert planned["payload"]["projection_generation"] == 9


def test_finalization_journals_until_neo4j_returns_then_hands_off(
    tmp_path,
    monkeypatch,
):
    commits = []
    value = runtime(tmp_path, neo_factory=lambda: FakeNeo(commits))
    monkeypatch.delenv("NEO4J_URI", raising=False)
    pending = value.submit_finalization(
        {
            "operation_id": "task-1",
            "operation_kind": "task_outcome",
            "final_state": "COMPLETED",
            "record_checksum": "a" * 64,
            "epoch": 7,
            "evidence": {"artifact": "sha256:result"},
        }
    )
    assert pending["status"] == "PENDING_DURABLE_COMMIT"
    assert value.journal.verify()["pending"] == 1

    handed_off = value.reconcile_primary_return(
        {"owner": "primary-xwing", "epoch": 8}
    )
    assert handed_off["status"] == "RELINQUISHED"
    assert handed_off["replay"]["remaining"] == 0
    assert len(commits) == 1


def test_memory_pressure_plan_is_deterministic(tmp_path):
    value = runtime(tmp_path)
    normal = value.memory_pressure_plan(
        {"available_mb": 3000, "total_mb": 14336}
    )
    emergency = value.memory_pressure_plan(
        {"available_mb": 700, "total_mb": 14336}
    )
    assert normal["state"] == "NORMAL"
    assert emergency["state"] == "EMERGENCY"
    assert emergency["payload"]["actions"][-1] == "reject_new_work"


def test_http_fence_blocks_ordinary_application_routes(tmp_path, monkeypatch):
    value = runtime(tmp_path)
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/api/tasks")
    def unsafe_task_create():
        return {"unsafe": True}

    app.include_router(
        build_degraded_control_router(
            lambda: "operator",
            runtime_factory=lambda: value,
        )
    )
    install_degraded_route_fence(app)
    monkeypatch.setenv("ASSISTX_DEGRADED_CONTROL_PLANE", "true")
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/api/degraded/status").status_code == 200
    blocked = client.post("/api/tasks", json={"title": "unsafe"})
    assert blocked.status_code == 503
    assert "degraded control-plane mode" in blocked.json()["detail"]
