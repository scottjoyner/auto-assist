"""Regression: actual record_event persists ledger before projection.

Unit uses a fake in-memory graph/session only; the second test verifies the
proposed shadow decision is strictly deny-only even with perfect synthetic
evidence. No event data or Neo4j credentials leave memory.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from assistx import swarm_core

SRC=Path(__file__).resolve().parents[1]/"src/assistx/event_projection_replay_shadow.py"
spec=importlib.util.spec_from_file_location("event_projection_replay_shadow",SRC)
assert spec and spec.loader
mod=importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name]=mod
spec.loader.exec_module(mod)


class Result:
    def __init__(self, obj=None):
        self.obj=obj
    def single(self):
        return self.obj
    def consume(self):
        return None


class Session:
    def __init__(self, store):
        self.store=store
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def run(self, query, args=None):
        params=args or {}
        if "MATCH (e:EventEnvelope)" in query and "e.payload_hash" in query:
            present=self.store.get("ledger")
            return Result(present)
        if "CREATE (e:SignalEvent:EventEnvelope" in query:
            assert self.store.get("ledger") is None
            self.store["ledger"]={
                "event_id":params["event_id"],
                "idempotency_key":params["idempotency_key"],
                "payload_hash":params["payload_hash"],
            }
            return Result()
        raise AssertionError("unexpected database query")


class FakeNeo:
    def __init__(self, store):
        self.store=store
    def _session(self):
        return Session(self.store)


def synthetic_event():
    return {
        "event_id":"scratch-acceptance-"+uuid4().hex,
        "event_type":"swarm.node.heartbeat",
        "source_repo":"synthetic-test",
        "source_service":"isolated-test",
        "node_id":"scratch-acceptance-node",
        "occurred_at":"2026-10-12T14:00:00Z",
        "idempotency_key":"scratch-acceptance-"+uuid4().hex,
        "correlation_id":str(uuid4()),
        "schema_version":"1.0",
        "subject":{"kind":"synthetic","id":"scratch-acceptance-1"},
        "payload":{"node_id":"scratch-acceptance-node","services":[]},
        "artifact_refs":[],
        "privacy":{"pii":False,"privacy_class":"internal","retention_class":"short"},
    }


def perfect_witness(**changes):
    args={
        "event_present":True,"digest_matches":True,
        "projection_receipt_present":True,
        "projection_receipt_digest_matches":True,
        "projection_transaction_committed":True,
        "writer_epoch_fenced":True,
        "producer_owner_verified":True,
        "queued_events_drained":True,
        "source_revision_bound":True,
        "positive_epoch":5,
        "synthetic_only":True,
    }
    args.update(changes)
    return mod.ReplayWitness(**args)


def test_actual_record_event_crash_after_persist_never_reconciles_on_retry():
    store={}
    event=synthetic_event()
    attempted=[]
    with patch.object(swarm_core,"reconcile_event",
                      side_effect=RuntimeError("synthetic crash after ledger commit")) as reconcile:
        with pytest.raises(RuntimeError,match="synthetic crash"):
            swarm_core.record_event(FakeNeo(store),event)
        assert reconcile.call_count == 1
    assert store.get("ledger") is not None
    with patch.object(swarm_core,"reconcile_event",side_effect=lambda *args: attempted.append(True)):
        retry=swarm_core.record_event(FakeNeo(store),event)
    assert retry=={
       "accepted":True,"event_id":event["event_id"],
       "deduped":True,"graph_reconciled":False,
    }
    assert attempted==[]  # a restart/retry cannot backfill a failed projection
    assert "projection_receipt" not in store


@pytest.mark.parametrize("change,reason",[
    ({"event_present":False},"HOLD_EVENT_ABSENT_NOT_A_REPLAY"),
    ({"digest_matches":False},"HOLD_DIGEST_CONFLICT_OR_UNKNOWN"),
    ({"digest_matches":None},"HOLD_DIGEST_CONFLICT_OR_UNKNOWN"),
    ({"projection_receipt_present":False},"HOLD_UNKNOWN_PROJECTION_EFFECT_NO_AUTOREPLAY"),
    ({"projection_receipt_digest_matches":False},"HOLD_UNKNOWN_PROJECTION_EFFECT_NO_AUTOREPLAY"),
    ({"projection_transaction_committed":None},"HOLD_UNKNOWN_PROJECTION_EFFECT_NO_AUTOREPLAY"),
    ({"writer_epoch_fenced":False},"HOLD_FENCE_PRODUCER_DRAIN_OR_SOURCE_UNVERIFIED"),
    ({"producer_owner_verified":False},"HOLD_FENCE_PRODUCER_DRAIN_OR_SOURCE_UNVERIFIED"),
    ({"queued_events_drained":False},"HOLD_FENCE_PRODUCER_DRAIN_OR_SOURCE_UNVERIFIED"),
    ({"source_revision_bound":False},"HOLD_FENCE_PRODUCER_DRAIN_OR_SOURCE_UNVERIFIED"),
    ({"positive_epoch":0},"HOLD_FENCE_PRODUCER_DRAIN_OR_SOURCE_UNVERIFIED"),
    ({"synthetic_only":False},"HOLD_UNTRUSTED_OR_NON_SYNTHETIC_WITNESS"),
])
def test_indeterminate_replay_never_authorized(change,reason):
    result=mod.qualify_replay(perfect_witness(**change))
    assert result.status==reason
    assert not result.event_replay_authorized
    assert not result.production_release_authorized
    assert not result.projection_confirmed


def test_perfect_atomic_receipt_proves_only_prior_projection_not_replay_permission():
    decision=mod.qualify_replay(perfect_witness())
    assert decision.status=="CONFIRMED_PROJECTED_NO_REPLAY_REQUIRED"
    assert decision.projection_confirmed
    assert not decision.event_replay_authorized
    assert not decision.production_release_authorized


def test_wrong_or_missing_witness_denied():
    for candidate in (None,{},object(),"synthetic"):
        result=mod.qualify_replay(candidate)
        assert result.status=="HOLD_UNTRUSTED_OR_NON_SYNTHETIC_WITNESS"
        assert not result.event_replay_authorized


def test_no_runtime_import_or_graph_write_code():
    content=SRC.read_text()
    for forbidden in ("MATCH (","MERGE (","CREATE (","docker ","neo4j","requests.","subprocess","api_events("):
        assert forbidden not in content
    assert "event_replay_authorized: bool = False" in content
