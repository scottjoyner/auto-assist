"""Isolated synthetic strict heartbeat/status guard source tests."""
from __future__ import annotations

import pytest

from assistx import legacy_task_writer_guard as guard
from assistx.neo4j_client import Neo4jClient


class Row:
    def __init__(self, data):
        self.data=data
    def single(self):
        return self.data


class FakeTransaction:
    def __init__(self, *, locks=1, active=True):
        self.locks=locks
        self.active=active
        self.calls=[]
    def run(self, query, parameters=None):
        self.calls.append((query,parameters))
        if len(self.calls)==1:
            return Row({"matching_rows":self.locks})
        return Row({"t":{"status":"RUNNING","claim_id":"current-claim"}} if self.active else None)


class FakeSession:
    def __init__(self, tx):
        self.tx=tx
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def execute_write(self,func,*args):
        return func(self.tx,*args)


class FakeNeo:
    def __init__(self, tx):
        self.tx=tx
        self.opens=0
    def _session(self):
        self.opens+=1
        return FakeSession(self.tx)


def run(neo, **overrides):
    kwargs=dict(task_id="synthetic-task",agent_id="ordinary-worker",
        claim_id="current-claim",session_id="synthetic-session",
        status="RUNNING",lease_seconds=60,metadata={"synthetic":True})
    kwargs.update(overrides)
    return guard.strict_legacy_heartbeat(neo,**kwargs)


def test_lock_precedes_eligible_task_mutation_and_provenance():
    tx=FakeTransaction()
    assert run(FakeNeo(tx))=={"status":"RUNNING","claim_id":"current-claim"}
    assert len(tx.calls)==2
    assert "WorkSupplyState {name:'canonical'}" in tx.calls[0][0]
    assert "legacy_heartbeat_serial" in tx.calls[0][0]
    q=tx.calls[1][0]
    for marker in (
        "collect(t0) AS matches","size(matches)=1",
        "t.claimed_by=$agent_id",
        "t.claim_id=$claim_id",
        "t.agent_session_id IS NULL OR t.agent_session_id=$session_id",
        "t.execution_attempt,0)>0","t.lease_expires_at_ts>timestamp()",
        "t.target_agent_id IS NULL","t.machine_authorized,false)=false",
        "t.machine_authorization_generation IS NULL",
        "t.machine_authorization_approval_id IS NULL",
        "t.machine_token_epoch IS NULL","t.projection_generation IS NULL",
        "t.hermes_claim_scope,false)=false",
        "NOT EXISTS {","DISPATCHED_AS",
    ):
        assert marker in q
    assert "SET t.agent_session_id=" not in q, "heartbeat cannot change claimed session"
    assert "t.status IN ['CLAIMED','RUNNING','PAUSING']" in q
    assert tx.calls[1][1]["metadata_json"]=='{"synthetic": true}'


@pytest.mark.parametrize("count",[0,2,3])
def test_coordination_lock_missing_or_duplicate_rolls_back(count):
    tx=FakeTransaction(locks=count)
    assert run(FakeNeo(tx)) is None
    assert len(tx.calls)==1


def test_protected_or_expired_heartbeat_denied_with_rollback():
    tx=FakeTransaction(active=False)
    assert run(FakeNeo(tx)) is None
    assert len(tx.calls)==2


@pytest.mark.parametrize("change",[
    {"task_id":""},{"agent_id":""},{"claim_id":None},{"claim_id":""},
    {"session_id":""},{"session_id":17},{"status":"DONE"},
    {"status":"FAILED"},{"status":"CANCELLED"},{"status":"PAUSING"},
    {"lease_seconds":0},{"lease_seconds":901},{"lease_seconds":-1},
    {"lease_seconds":True},{"lease_seconds":1.5},
    {"metadata":{"nan":float("nan")}},
    {"metadata":{"object":object()}},
    {"metadata":{"oversize":"x"*5000}},
    {"metadata":"not-json-map"},
])
def test_bad_input_denied_without_opening_database(change):
    fake=FakeNeo(FakeTransaction())
    assert run(fake,**change) is None
    assert fake.opens==0


@pytest.mark.parametrize("status",[None,"RUNNING"])
def test_active_heartbeat_status_values_allowed(status):
    fake=FakeNeo(FakeTransaction())
    assert run(fake,status=status) is not None
    assert fake.opens==1


def test_direct_status_strict_quarantine_rejects_all_unauthenticated_writes():
    for status in ("READY","RUNNING","DONE","FAILED","CANCELLED","PAUSED"):
        with pytest.raises(guard.LegacyWriterDenied,match="unattributed_direct"):
            guard.reject_unattributed_status_change("synthetic-task",status)


def test_wrapper_default_compatibility_and_explicit_strict_mode(monkeypatch):
    neo=Neo4jClient.__new__(Neo4jClient)
    recorded=[]
    class Reader:
        def single(self):
            return {"t":{"status":"RUNNING"}}
    class S:
        def __enter__(self):
            return self
        def __exit__(self,*a):
            return False
        def run(self,query,params=None):
            recorded.append(("legacy",params))
            return Reader()
    monkeypatch.setattr(Neo4jClient,"_session",lambda self:S())
    monkeypatch.delenv("ASSISTX_ENABLE_STRICT_LEGACY_HEARTBEAT",raising=False)
    monkeypatch.delenv("ASSISTX_ENABLE_STRICT_UNATTRIBUTED_TASK_STATUS",raising=False)
    assert neo.heartbeat_task(
        "synthetic","ordinary-worker",status="RUNNING",claim_id=None,
    )["status"]=="RUNNING"
    assert len(recorded)==1
    monkeypatch.setenv("ASSISTX_ENABLE_STRICT_LEGACY_HEARTBEAT","1")
    assert neo.heartbeat_task(
        "synthetic","ordinary-worker",status="RUNNING",claim_id=None,
    ) is None
    assert len(recorded)==1

    neo.update_task_status("synthetic","DONE")
    assert len(recorded)==2
    monkeypatch.setenv("ASSISTX_ENABLE_STRICT_UNATTRIBUTED_TASK_STATUS","1")
    with pytest.raises(guard.LegacyWriterDenied):
        neo.update_task_status("synthetic","DONE")
    assert len(recorded)==2
