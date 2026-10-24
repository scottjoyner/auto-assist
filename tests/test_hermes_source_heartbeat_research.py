"""Fail-closed synthetic heartbeat source and receipt tests."""
from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from assistx import hermes_source_heartbeat_research as h
from assistx import hermes_source_generation_research as g


def _valid():
    return {
        "task_id":"synthetic-task","claim_id":"synthetic-claim",
        "session_id":"synthetic-session","attempt":2,"ttl_ms":15000,
        "expected_generation":3,
    }


def _ack():
    return {
        "task_id":"synthetic-task","claim_id":"synthetic-claim",
        "session_id":"synthetic-session","attempt":2,
        "status":"RUNNING","heartbeat_by":"hermes-local",
        "heartbeat_at_ts":400000,"db_now":400000,
        "lease_expiry":415000,
    }


def test_ledger_before_task_and_db_clock_predicates():
    q=h._RENEW
    assert "SET t.hermes_fence_check_epoch =" in q
    assert q.index("SET t.hermes_fence_check_epoch")<q.index("WHERE t.claimed_by")
    for pred in (
        "t.claimed_by=$owner",
        "t.agent_session_id=$session_id",
        "t.claim_id=$claim_id",
        "t.execution_attempt=$attempt",
        "t.status IN ['CLAIMED','RUNNING']",
        "coalesce(t.lease_expires_at_ts,0) > db_now",
        "(t.target_agent_id IS NULL OR t.target_agent_id=$owner)",
        "(t.heartbeat_by IS NULL OR t.heartbeat_by=$owner)",
        "t.completed_by IS NULL","t.failed_by IS NULL",
        "t.paused_from_agent_id IS NULL",
        "t.migration_target_agent_id IS NULL",
    ):
        assert pred in q
    assert "timestamp() AS db_now" in q
    assert "db_now+$ttl_ms" in q
    assert "$now_ms" not in q
    assert "now_ms" not in inspect.signature(h.renew_candidate_lease).parameters


@pytest.mark.parametrize("changes",[
    {"task_id":""},{"task_id":"../other"},{"claim_id":"bad id"},
    {"session_id":""},{"session_id":"wrong/slash"},
    {"attempt":0},{"attempt":True},{"attempt":"2"},
    {"ttl_ms":0},{"ttl_ms":999},{"ttl_ms":60001},
    {"ttl_ms":True},{"ttl_ms":1.5},
])
def test_invalid_identity_or_ttl_denied_before_ledger(monkeypatch,changes):
    called=[]
    monkeypatch.setattr(h,"advance_candidate_generation",
                        lambda *a,**k: called.append(True))
    d=_valid()
    d.update(changes)
    with pytest.raises(g.GenerationProtocolError,match="invalid_heartbeat"):
        h.renew_candidate_lease(None,**d)
    assert called==[]


def test_valid_db_row_and_generation_still_return_hold(monkeypatch):
    def fake(_session,*,expected_generation,mutate):
        class Tx:
            def run(self,q,params):
                assert q==h._RENEW
                assert params["ttl_ms"]==15000
                out=MagicMock()
                out.single.return_value=_ack()
                return out
        assert mutate(Tx()) is True
        return {"generation":expected_generation+1}
    monkeypatch.setattr(h,"advance_candidate_generation",fake)
    out=h.renew_candidate_lease(None,**_valid())
    assert out["candidate_generation"]==4
    assert out["source_task_lease_readback_in_tx"]
    assert out["source_generation_writers_complete"] is False
    assert out["external_resource_lease_verified"] is False
    assert out["independent_custody_verified"] is False
    assert out["production_dispatch_authorized"] is False
    assert out["decision"]=="HOLD"


@pytest.mark.parametrize("changes",[
    {"task_id":"mismatch"},{"claim_id":"mismatch"},{"session_id":"old"},
    {"attempt":1},{"attempt":True},{"status":"PAUSED"},
    {"heartbeat_by":"legacy-worker"},
    {"heartbeat_at_ts":399999},{"heartbeat_at_ts":"400000"},
    {"lease_expiry":414999},{"lease_expiry":True},
    {"db_now":"400000"},{"db_now":True},
])
def test_inconsistent_db_row_must_refuse_ack(monkeypatch,changes):
    def fake(_session,*,expected_generation,mutate):
        class Tx:
            def run(self,q,params):
                row=_ack()
                row.update(changes)
                answer=MagicMock()
                answer.single.return_value=row
                return answer
        assert mutate(Tx()) is False
        raise g.GenerationProtocolError("task_mutation_not_acknowledged")
    monkeypatch.setattr(h,"advance_candidate_generation",fake)
    with pytest.raises(g.GenerationProtocolError):
        h.renew_candidate_lease(None,**_valid())


def test_no_row_must_fail_before_increment(monkeypatch):
    def fake(_session,*,expected_generation,mutate):
        class Tx:
            def run(self,q,params):
                answer=MagicMock()
                answer.single.return_value=None
                return answer
        assert mutate(Tx()) is False
        raise g.GenerationProtocolError("task_mutation_not_acknowledged")
    monkeypatch.setattr(h,"advance_candidate_generation",fake)
    with pytest.raises(g.GenerationProtocolError):
        h.renew_candidate_lease(None,**_valid())


def test_live_sources_do_not_import_heartbeat_research():
    root=Path(__file__).resolve().parents[1]/"src/assistx"
    for path in root.rglob("*.py"):
        if path.name.startswith("hermes_source_"):
            continue
        assert "hermes_source_heartbeat_research" not in path.read_text(),path
