"""Research-only actual AssistX swarm_core behavior with a fake graph session.

No Neo4j container, network connection, real endpoint or production mutation.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from assistx import swarm_core


class Result:
    def __init__(self, row=None):
        self.row = row

    def single(self):
        return self.row

    def consume(self):
        return None


class Graph:
    def __init__(self):
        self.envelope = None
        self.creates = 0
        self.conflicts = 0

    @contextmanager
    def _session(self):
        yield self

    def run(self, query, args):
        if "MATCH (e:EventEnvelope)" in query:
            if self.envelope is None:
                return Result()
            if (args.get("event_id") == self.envelope["event_id"]
                    or args.get("idempotency_key") == self.envelope["idempotency_key"]):
                return Result(dict(self.envelope))
            return Result()
        if "CREATE (e:SignalEvent:EventEnvelope" in query:
            self.creates += 1
            self.envelope = {
                "event_id": args["event_id"],
                "idempotency_key": args["idempotency_key"],
                "payload_hash": args["payload_hash"],
            }
            return Result()
        if "CREATE (c:EventConflict" in query:
            self.conflicts += 1
            return Result()
        raise AssertionError("unmodelled synthetic query; deny")


def envelope():
    return {
        "event_id": "evt_synthetic_001",
        "event_type": "assignment.proposed",
        "source_repo": "synthetic",
        "source_service": "offline-test",
        "node_id": "offline-node",
        "occurred_at": "2026-10-15T11:00:00+00:00",
        "idempotency_key": "effect-case",
        "schema_version": "2026-06-08.v1",
        "subject": {"kind": "task", "id": "task-synthetic"},
        "payload": {"task_id": "task-synthetic"},
        "artifact_refs": [],
        "privacy": {"pii": False, "privacy_class": "public",
                    "retention_class": "keep"},
    }


def test_post_envelope_projection_failure_then_retry_dedupes_without_reconcile(monkeypatch):
    graph = Graph()
    projection_calls = []

    def fail_after_envelope(_neo, _event):
        projection_calls.append(1)
        raise RuntimeError("synthetic graph projection failure")

    monkeypatch.setattr(swarm_core, "reconcile_event", fail_after_envelope)
    event = envelope()
    with pytest.raises(RuntimeError, match="synthetic graph projection failure"):
        swarm_core.record_event(graph, event)
    assert graph.creates == 1
    assert len(projection_calls) == 1
    second = swarm_core.record_event(graph, event)
    assert second["accepted"] is True
    assert second["deduped"] is True
    assert second["graph_reconciled"] is False
    assert len(projection_calls) == 1


def test_changed_event_same_key_yields_conflict_not_exact_duplicate(monkeypatch):
    graph = Graph()
    monkeypatch.setattr(swarm_core, "reconcile_event", lambda *args: None)
    event = envelope()
    first = swarm_core.record_event(graph, event)
    assert first["accepted"] is True
    altered = dict(event, event_id="evt_synthetic_002",
                   payload={"task_id": "task-synthetic", "altered": True})
    with pytest.raises(swarm_core.EventConflictError):
        swarm_core.record_event(graph, altered)
    assert graph.creates == 1
    assert graph.conflicts == 1


def test_concurrent_same_key_different_event_ids_interleave_without_key_guard(monkeypatch):
    """Forced fake-session race: two SELECT misses, then two independent CREATEs."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    class RaceGraph:
        def __init__(self):
            self.barrier = Barrier(2)
            self.lock = Lock()
            self.created = []

        @contextmanager
        def _session(self):
            yield self

        def run(self, query, args):
            if "MATCH (e:EventEnvelope)" in query:
                self.barrier.wait(timeout=5)
                return Result()
            if "CREATE (e:SignalEvent:EventEnvelope" in query:
                with self.lock:
                    self.created.append((args["event_id"], args["idempotency_key"]))
                return Result()
            raise AssertionError("unmodelled concurrent fake graph query")

    graph = RaceGraph()
    monkeypatch.setattr(swarm_core, "reconcile_event", lambda *_: None)
    left = envelope()
    right = dict(envelope(), event_id="evt_synthetic_other")
    with ThreadPoolExecutor(max_workers=2) as pool:
        submitted = [pool.submit(swarm_core.record_event, graph, event)
                     for event in (left, right)]
        results = [item.result(timeout=9) for item in submitted]
    assert len(graph.created) == 2
    assert graph.created[0][0] != graph.created[1][0]
    assert {pair[1] for pair in graph.created} == {"effect-case"}
    assert all(r["accepted"] and not r["deduped"] for r in results)
    # This is a deterministic source interleaving, NOT a physical Neo4j race.