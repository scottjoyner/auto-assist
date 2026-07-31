from __future__ import annotations

import json

import pytest

from assistx.operational_state import OperationalStateStore


class FakeGraph:
    def __init__(self):
        self.rows = {}

    def execute_command(self, command, graph, query, *args):
        params = json.loads(args[-1]) if args and args[-2] == "PARAMS" else {}
        if command == "GRAPH.QUERY" and "MERGE" in query:
            existing = self.rows.get(params["record_id"])
            created = existing[7] if existing else params["now"]
            row = [
                params["record_id"],
                params["kind"],
                params["logical_id"],
                params["state"],
                params.get("owner"),
                params["epoch"],
                params["payload_json"],
                created,
                params["now"],
                params["expires_at_ms"],
                params["checksum"],
            ]
            self.rows[params["record_id"]] = row
            return [["r.created_at_ms"], [[created]], []]
        if command == "GRAPH.RO_QUERY":
            row = self.rows.get(params["record_id"])
            return [[], [row] if row else [], []]
        if command == "GRAPH.QUERY" and "DELETE" in query:
            expired = [key for key, row in self.rows.items() if row[9] <= params["now"]]
            for key in expired[: params["limit"]]:
                del self.rows[key]
            return [["count(r)"], [[len(expired[: params["limit"]])]], []]
        raise AssertionError((command, query, params))


def test_lease_is_bounded_and_epoch_monotonic():
    clock = [1_000]
    store = OperationalStateStore(FakeGraph(), clock_ms=lambda: clock[0])
    first = store.acquire_lease(logical_id="fleet-primary", owner="beelink", epoch=7, ttl_seconds=20)
    assert first.state == "ACTIVE"
    assert first.expires_at_ms == 21_000

    with pytest.raises(RuntimeError, match="another owner"):
        store.acquire_lease(logical_id="fleet-primary", owner="xwing", epoch=8)
    with pytest.raises(RuntimeError, match="moved backwards"):
        store.acquire_lease(logical_id="fleet-primary", owner="beelink", epoch=6)


def test_expired_records_are_not_authoritative():
    clock = [10_000]
    graph = FakeGraph()
    store = OperationalStateStore(graph, clock_ms=lambda: clock[0])
    store.upsert(kind="heartbeat", logical_id="xwing", state="ONLINE", ttl_seconds=5)
    assert store.get("heartbeat", "xwing") is not None
    clock[0] = 16_000
    assert store.get("heartbeat", "xwing") is None
    assert store.sweep_expired() == 1


def test_finalize_commits_neo4j_before_marking_operational_state():
    clock = [1_000]
    graph = FakeGraph()
    store = OperationalStateStore(graph, clock_ms=lambda: clock[0])
    record = store.upsert(kind="recovery_intent", logical_id="incident-1", state="ACTIVE")
    commits = []

    result = store.finalize(
        record,
        final_state="COMPLETED",
        neo4j_commit=lambda envelope: commits.append(envelope) or "durable",
        evidence={"probe": "passed"},
    )
    assert result == "durable"
    assert commits[0]["final_state"] == "COMPLETED"
    finalized = store.get("recovery_intent", "incident-1")
    assert finalized is not None
    assert finalized.state == "COMPLETED"
    assert finalized.payload["durable_commit_id"] == commits[0]["commit_id"]


def test_failed_neo4j_commit_does_not_claim_final_success():
    store = OperationalStateStore(FakeGraph(), clock_ms=lambda: 1_000)
    record = store.upsert(kind="delegation", logical_id="task-1", state="ACTIVE")

    def fail(_envelope):
        raise RuntimeError("neo4j unavailable")

    with pytest.raises(RuntimeError, match="neo4j unavailable"):
        store.finalize(record, final_state="COMPLETED", neo4j_commit=fail)
    assert store.get("delegation", "task-1").state == "ACTIVE"
