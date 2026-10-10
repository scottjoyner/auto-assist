"""Bounded post-hoc inspection receipts; synthetic Neo4j only and no real writes."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from assistx.fleet_hardware_inspection import (
    InspectionInput,
    InspectionSchemaUnavailable,
    build_inspection_router,
    list_inspections,
    persist_inspection,
    prepare_inspection_receipt,
)

SNAP = "2026-10-10T16-00-00Z"
DIGEST = "a" * 64
CID = "sample-correlation-123"


def preview(*, fresh: bool = True) -> dict[str, Any]:
    return {
        "snapshot": SNAP,
        "snapshot_freshness": "FRESH" if fresh else "STALE",
        "evidence_digest_sha256": DIGEST,
        "admission_allowed": False,
        "dispatch_allowed": False,
        "requirements": {"ram_gib": 20.0, "gpu_vram_gib": 16.0, "data_host": None},
        "checked_nodes": 3,
        "truncated": False,
        "nodes": [{"node_id": "gpu-node", "dispatch": "DENIED_NO_AUTHORITY"}],
    }


class MemoryNeo:
    """Fake graph accepts ONLY supported receipt queries, never task-state writes."""

    def __init__(self):
        self.groups = {CID: {"current_state": "completed"}}
        self.receipts: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = 0
        self.constraint_present = True
        self.lock_constraint_present = True
        self.lock_counts: dict[str, int] = {}

    def _session(self):
        return MemorySession(self)

    def close(self):
        self.closed += 1


class MemorySession:
    def __init__(self, db: MemoryNeo):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def run(self, query: str, params: dict[str, Any]):
        self.db.calls.append((query, params))
        assert "TraceEvent" not in query
        assert "current_state" not in query
        assert ":Task" not in query
        assert "CREATE (g:" not in query
        if "SHOW CONSTRAINTS" in query:
            return MemoryResult({"matched": int(self.db.constraint_present) + int(self.db.lock_constraint_present)})
        if "ON CREATE SET" in query:
            assert "FleetHardwareInspectionLock" in query
            assert "fence_counter" in query
            assert "WITH collect(candidate) AS groups" in query
            cid = params["correlation_id"]
            if cid not in self.db.groups or (
                params["receipt_id"] not in self.db.receipts
                and sum(r["correlation_id"] == cid for r in self.db.receipts.values()) >= 20
            ):
                return MemoryResult(None)
            item = self.db.receipts.setdefault(
                params["receipt_id"],
                {
                    **params,
                    "recorded_at_ms": 123456,
                },
            )
            return MemoryResult({"receipt_id": item["receipt_id"], "recorded_at_ms": item["recorded_at_ms"]})
        if "ORDER BY" in query:
            return MemoryResult(
                [
                    {
                        **item,
                        "kind": item["mode"],
                        "requirements_json": item["requirements_json"],
                    }
                    for item in self.db.receipts.values()
                    if item["correlation_id"] == params["correlation_id"]
                ][:20]
            )
        raise AssertionError("unexpected query")


class MemoryResult:
    def __init__(self, item):
        self.item = item

    def single(self):
        return self.item if isinstance(self.item, dict) else None

    def __iter__(self):
        return iter(self.item if isinstance(self.item, list) else [])


def test_receipt_deterministic_and_non_authoritative():
    a = prepare_inspection_receipt(CID, preview())
    b = prepare_inspection_receipt(CID, preview())
    assert a == b
    assert a["receipt_id"].startswith("fleet-hardware-inspection:")
    assert a["historical_decision_proof"] is False
    assert a["dispatch_allowed"] is False
    assert "/nas" not in str(a)


@pytest.mark.parametrize(
    "value",
    [
        {"dispatch_allowed": True},
        {"admission_allowed": True},
        {"snapshot_freshness": "STALE"},
        {"evidence_digest_sha256": "corrupt"},
        {"requirements": {"ram_gib": float("nan"), "gpu_vram_gib": 16, "data_host": None}},
        {"nodes": [{"dispatch": "ALLOWED"}]},
    ],
)
def test_bad_evidence_fails_closed(value):
    data = preview()
    data.update(value)
    with pytest.raises(ValueError):
        prepare_inspection_receipt(CID, data)


def test_existing_trace_link_idempotency_and_no_state_change():
    neo = MemoryNeo()
    receipt = prepare_inspection_receipt(CID, preview())
    first = persist_inspection(neo, receipt, authenticated_actor="operator-1")
    second = persist_inspection(neo, receipt, authenticated_actor="operator-1")
    assert first == second
    assert len(neo.receipts) == 1
    assert neo.groups[CID]["current_state"] == "completed"
    records = list_inspections(neo, CID)
    assert len(records) == 1
    assert records[0]["historical_decision_proof"] is False
    assert records[0]["dispatch_allowed"] is False
    assert records[0]["actor"] == "operator-1"


def test_cannot_create_new_trace_group():
    neo = MemoryNeo()
    receipt = prepare_inspection_receipt("nonexistent", preview())
    assert persist_inspection(neo, receipt, authenticated_actor="operator") is None
    assert list_inspections(neo, "nonexistent") == []
    assert len(neo.groups) == 1
    assert len(neo.receipts) == 0


def test_max_inspections_bounded():
    neo = MemoryNeo()
    for index in range(20):
        d = preview()
        d["requirements"] = {"ram_gib": float(index), "gpu_vram_gib": 0.0, "data_host": None}
        r = prepare_inspection_receipt(CID, d)
        assert persist_inspection(neo, r, authenticated_actor="operator")
    d = preview()
    d["requirements"] = {"ram_gib": 21.0, "gpu_vram_gib": 0.0, "data_host": None}
    assert persist_inspection(neo, prepare_inspection_receipt(CID, d), authenticated_actor="operator") is None
    assert len(neo.receipts) == 20
    # Lost acknowledgments are safely retried, even when a trace is at quota.
    original = preview()
    original["requirements"] = {"ram_gib": 0.0, "gpu_vram_gib": 0.0, "data_host": None}
    replay = prepare_inspection_receipt(CID, original)
    assert persist_inspection(neo, replay, authenticated_actor="operator")
    assert len(neo.receipts) == 20


def test_disabled_by_default_no_db_calls():
    neo = MemoryNeo()
    app = FastAPI()
    app.include_router(
        build_inspection_router(
            auth_dependency=lambda: "operator",
            neo_factory=lambda: neo,
            root_provider=lambda: "/unused",
            write_enabled=lambda: False,
        )
    )
    c = TestClient(app)
    result = c.post(
        f"/api/fleet/trace-inspections/{CID}",
        json={"ram_gib": 20.0, "confirm_post_hoc_observation": True},
        headers={"X-AssistX-Operator-Intent": "fleet-hardware-inspection-v1"},
    )
    assert result.status_code == 503
    assert neo.calls == []
    assert len(neo.receipts) == 0


def test_operator_intent_and_authentication_required():
    neo = MemoryNeo()
    app = FastAPI()
    app.include_router(
        build_inspection_router(
            auth_dependency=lambda: "operator",
            neo_factory=lambda: neo,
            root_provider=lambda: "/unused",
            write_enabled=lambda: True,
        )
    )
    c = TestClient(app)
    path = f"/api/fleet/trace-inspections/{CID}"
    body = {"ram_gib": 20.0, "gpu_vram_gib": 16.0, "confirm_post_hoc_observation": True}
    assert c.post(path, json=body).status_code == 403
    assert (
        c.post(
            path,
            json={**body, "confirm_post_hoc_observation": False},
            headers={"X-AssistX-Operator-Intent": "fleet-hardware-inspection-v1"},
        ).status_code
        == 403
    )
    assert (
        c.post(
            path,
            json={**body, "unknown": "unsafe"},
            headers={"X-AssistX-Operator-Intent": "fleet-hardware-inspection-v1"},
        ).status_code
        == 422
    )
    assert not neo.calls

    def reject():
        raise HTTPException(401, "unauthorized")

    locked = FastAPI()
    locked.include_router(
        build_inspection_router(auth_dependency=reject, neo_factory=lambda: neo, write_enabled=lambda: True)
    )
    assert TestClient(locked).get(path).status_code == 401
    assert TestClient(locked).post(path, json=body).status_code == 401


def test_bounded_get_returns_empty_without_adding_trace():
    neo = MemoryNeo()
    app = FastAPI()
    app.include_router(
        build_inspection_router(
            auth_dependency=lambda: "operator", neo_factory=lambda: neo, write_enabled=lambda: False
        )
    )
    c = TestClient(app)
    result = c.get(f"/api/fleet/trace-inspections/{CID}")
    assert result.status_code == 200
    assert result.json()["receipts"] == []
    assert result.json()["dispatch_allowed"] is False
    assert all("MERGE" not in stmt for stmt, _ in neo.calls)


def test_strict_request_rejects_nonfinite():
    with pytest.raises(Exception):
        InspectionInput(ram_gib=float("nan"), confirm_post_hoc_observation=True)
    with pytest.raises(Exception):
        InspectionInput(ram_gib=float("inf"), confirm_post_hoc_observation=True)
    with pytest.raises(Exception):
        InspectionInput(ram_gib="20", confirm_post_hoc_observation=True)


def test_explicit_operator_post_succeeds_only_with_evidence_matching_private_source(monkeypatch):
    from unittest.mock import Mock

    neo = MemoryNeo()
    read = Mock(return_value=preview())
    monkeypatch.setattr("assistx.fleet_hardware_inspection.hardware_preview", read)
    app = FastAPI()
    app.include_router(
        build_inspection_router(
            auth_dependency=lambda: "operator-1",
            neo_factory=lambda: neo,
            root_provider=lambda: "/synthetic-private-source",
            write_enabled=lambda: True,
        )
    )
    client = TestClient(app)
    endpoint = f"/api/fleet/trace-inspections/{CID}"
    body = {"ram_gib": 20.0, "gpu_vram_gib": 16.0, "confirm_post_hoc_observation": True}
    header = {"X-AssistX-Operator-Intent": "fleet-hardware-inspection-v1"}
    response = client.post(endpoint, json=body, headers=header)
    assert response.status_code == 200
    result = response.json()
    assert result["historical_decision_proof"] is False
    assert result["admission_allowed"] is False
    assert result["dispatch_allowed"] is False
    assert result["snapshot"] == SNAP
    assert len(neo.receipts) == 1
    repeat = client.post(endpoint, json=body, headers=header)
    assert repeat.status_code == 200
    assert repeat.json()["receipt_id"] == result["receipt_id"]
    assert len(neo.receipts) == 1
    read.assert_called()
    assert read.call_args.kwargs["min_ram_gib"] == 20.0
    listed = client.get(endpoint)
    assert listed.status_code == 200
    assert len(listed.json()["receipts"]) == 1
    assert neo.groups[CID]["current_state"] == "completed"


def test_missing_neo4j_uniqueness_constraint_denies_every_write():
    neo = MemoryNeo()
    neo.constraint_present = False
    receipt = prepare_inspection_receipt(CID, preview())
    with pytest.raises(InspectionSchemaUnavailable):
        persist_inspection(neo, receipt, authenticated_actor="operator")
    assert not neo.receipts
    assert all("ON CREATE SET" not in query for query, _ in neo.calls)
    assert neo.groups[CID]["current_state"] == "completed"


def test_missing_constraint_returns_503_on_authenticated_explicit_post(monkeypatch):
    from unittest.mock import Mock

    neo = MemoryNeo()
    neo.constraint_present = False
    monkeypatch.setattr("assistx.fleet_hardware_inspection.hardware_preview", Mock(return_value=preview()))
    app = FastAPI()
    app.include_router(
        build_inspection_router(
            auth_dependency=lambda: "operator",
            neo_factory=lambda: neo,
            root_provider=lambda: "/authorized-fixture",
            write_enabled=lambda: True,
        )
    )
    result = TestClient(app).post(
        f"/api/fleet/trace-inspections/{CID}",
        json={"ram_gib": 20.0, "gpu_vram_gib": 16.0, "confirm_post_hoc_observation": True},
        headers={"X-AssistX-Operator-Intent": "fleet-hardware-inspection-v1"},
    )
    assert result.status_code == 503
    assert "schema prerequisite" in result.json()["detail"]
    assert len(neo.receipts) == 0


def test_missing_lock_uniqueness_constraint_denies_every_write():
    neo = MemoryNeo()
    neo.lock_constraint_present = False
    receipt = prepare_inspection_receipt(CID, preview())
    with pytest.raises(InspectionSchemaUnavailable):
        persist_inspection(neo, receipt, authenticated_actor="operator")
    assert not neo.receipts
    assert all("ON CREATE SET" not in query for query, _ in neo.calls)
    assert neo.groups[CID]["current_state"] == "completed"
