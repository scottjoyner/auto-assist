"""Fail-closed legacy completion ownership tests; no real database required."""
from __future__ import annotations

import pytest

from assistx import legacy_task_completion as legacy


class Row:
    def __init__(self,data):self.data=data
    def single(self):return self.data


class FakeTx:
    def __init__(self,count=1,status="CLAIMED",fresh=True,eligible=True):
        self.count=count
        self.status=status
        self.fresh=fresh
        self.eligible=eligible
        self.calls=[]
    def run(self,query,params=None):
        self.calls.append((query,params))
        if len(self.calls)==1:
            return Row({"matching_rows":self.count})
        return Row({"status":self.status,"lease_fresh":self.fresh} if self.eligible else None)


def guarded(tx,**changes):
    params={"task_id":"synthetic-task","agent_id":"ordinary-worker",
            "claim_id":"correct-opaque-claim","session_id":None}
    params.update(changes)
    return legacy.require_current_legacy_completion(tx,**params)


def test_lock_then_postlock_claim_read():
    tx=FakeTx()
    assert guarded(tx)=="CLAIMED"
    assert len(tx.calls)==2
    assert "MATCH (w:WorkSupplyState {name:'canonical'})" in tx.calls[0][0]
    assert "legacy_completion_serial" in tx.calls[0][0]
    q=tx.calls[1][0]
    for marker in (
        "t.claimed_by=$agent_id","t.claim_id=$claim_id",
        "t.execution_attempt,0)>0","t.target_agent_id IS NULL",
        "t.machine_authorization_generation IS NULL",
        "t.machine_authorization_approval_id IS NULL",
        "t.machine_token_epoch IS NULL","t.projection_generation IS NULL",
        "t.hermes_claim_scope,false)=false",
        "t.lease_expires_at_ts>timestamp() AS lease_fresh",
    ):
        assert marker in q


@pytest.mark.parametrize("count",[0,2,3])
def test_missing_or_duplicate_canonical_lock_no_task_read(count):
    tx=FakeTx(count=count)
    with pytest.raises(legacy.LegacyTaskCompletionDenied,match="lock_not_unique"):
        guarded(tx)
    assert len(tx.calls)==1


@pytest.mark.parametrize("changes",[
    {"task_id":""},{"agent_id":""},{"claim_id":None},
    {"claim_id":""},{"session_id":""},{"session_id":17},
])
def test_bad_legacy_claim_fails_before_any_query(changes):
    tx=FakeTx()
    with pytest.raises(legacy.LegacyTaskCompletionDenied,match="missing_exact_legacy_claim"):
        guarded(tx,**changes)
    assert not tx.calls


@pytest.mark.parametrize("status",["READY","PAUSED","PAUSING","UNRECOGNIZED"])
def test_inactive_legacy_claim_is_denied(status):
    with pytest.raises(legacy.LegacyTaskCompletionDenied,match="inactive_legacy_claim"):
        guarded(FakeTx(status=status))


def test_expired_lease_cannot_complete():
    with pytest.raises(legacy.LegacyTaskCompletionDenied,match="expired_legacy_lease"):
        guarded(FakeTx(fresh=False))


def test_protected_claim_denied():
    with pytest.raises(legacy.LegacyTaskCompletionDenied,match="protected_or_stale"):
        guarded(FakeTx(eligible=False))


@pytest.mark.parametrize("status",["DONE","FAILED","CANCELLED"])
def test_terminal_status_only_later_idempotency_branch_may_read(status):
    assert guarded(FakeTx(status=status,fresh=False))==status


def test_opt_in_strict_mode_preserves_existing_default_compatibility(monkeypatch):
    from assistx.neo4j_client import Neo4jClient

    neo=Neo4jClient.__new__(Neo4jClient)
    calls=[]
    def legacy_completion(self, **kwargs):
        calls.append(kwargs)
        return {"status":"synthetic-compatibility"}
    monkeypatch.setattr(Neo4jClient, "_complete_task_unfenced",legacy_completion)
    monkeypatch.delenv("ASSISTX_ENABLE_STRICT_LEGACY_COMPLETION",raising=False)
    response=neo.complete_task("synthetic-task","ordinary-worker","DONE",claim_id=None)
    assert response=={"status":"synthetic-compatibility"}
    assert len(calls)==1
    assert calls[0]["claim_id"] is None
    monkeypatch.setenv("ASSISTX_ENABLE_STRICT_LEGACY_COMPLETION","1")
    assert neo.complete_task("synthetic-task","ordinary-worker","DONE",claim_id=None) is None
    assert len(calls)==1
