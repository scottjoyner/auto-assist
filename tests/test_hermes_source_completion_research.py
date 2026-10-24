"""Source-ledger completion prototype regression, never production."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from assistx import hermes_source_completion_research as c
from assistx import hermes_source_generation_research as g


def test_query_locks_task_and_checks_claim_before_status_change():
    sql=c._COMPLETE
    tokens=(
        "SET t.hermes_fence_check_epoch =",
        "WITH t", "WHERE t.claimed_by=$owner",
        "t.agent_session_id=$session_id",
        "t.claim_id=$claim_id",
        "t.execution_attempt=$attempt",
        "t.status='RUNNING'",
        "coalesce(t.lease_expires_at_ts,0) > timestamp()",
        "t.completed_by IS NULL",
        "SET t.status='DONE'",
    )
    for token in tokens:
        assert token in sql
    assert sql.index(tokens[0]) < sql.index(tokens[1]) < sql.index(tokens[2])
    assert sql.index(tokens[2]) < sql.index(tokens[-1])


@pytest.mark.parametrize("field",[
    "t.claimed_by=$owner",
    "t.agent_session_id=$session_id",
    "t.claim_id=$claim_id",
    "t.execution_attempt=$attempt",
    "t.status='RUNNING'",
    "coalesce(t.lease_expires_at_ts,0) > timestamp()",
    "t.completed_by IS NULL",
    "t.failed_by IS NULL",
    "t.paused_from_agent_id IS NULL",
    "t.migration_target_agent_id IS NULL",
])
def test_every_identity_or_effect_denial_predicate_is_mandatory(field):
    assert field in c._COMPLETE
    assert field not in c._COMPLETE.replace(field,"false",1)


@pytest.mark.parametrize("override",[
    {"task_id":""}, {"task_id":"../../secret"},
    {"claim_id":""}, {"claim_id":"slash/a"},
    {"session_id":""}, {"session_id":"bad session"},
    {"attempt":0}, {"attempt":True},
])
def test_invalid_claim_identity_fails_before_ledger_mutation(monkeypatch,override):
    calls=[]
    monkeypatch.setattr(c,"advance_candidate_generation",
                        lambda *a,**k:calls.append(1))
    args=dict(expected_generation=3,task_id="task1",claim_id="claim1",
              session_id="session1",attempt=1)
    args.update(override)
    with pytest.raises(g.GenerationProtocolError,match="invalid_completion"):
        c.complete_candidate_claim(None,**args)
    assert calls==[]


def test_receipt_does_not_claim_external_effect_or_custody(monkeypatch):
    def run(_session,*,expected_generation,mutate):
        class Tx:
            def run(self,sql,params):
                m=MagicMock()
                m.single.return_value={
                    "task_id":params["task_id"],
                    "claim_id":params["claim_id"],
                    "session_id":params["session_id"],
                    "attempt":params["attempt"],
                    "status":"DONE","completed_by":"hermes-local",
                }
                return m
        assert mutate(Tx()) is True
        return {"generation":expected_generation+1}
    monkeypatch.setattr(c,"advance_candidate_generation",run)
    out=c.complete_candidate_claim(
        None,expected_generation=3,task_id="task1",claim_id="claim1",
        session_id="session1",attempt=1,
    )
    assert out["candidate_generation"]==4
    assert out["source_task_row_readback_in_tx"] is True
    assert not out["resource_side_completion_verified"]
    assert not out["source_generation_writers_complete"]
    assert not out["production_dispatch_authorized"]
    assert out["decision"]=="HOLD"


@pytest.mark.parametrize("wrong",[
    {"task_id":"different"}, {"claim_id":"old"},
    {"session_id":"wrong"}, {"attempt":2},
    {"status":"READY"}, {"completed_by":"different"},
])
def test_nonmatching_database_ack_must_not_increment_generation(monkeypatch,wrong):
    def simulate(_session,*,expected_generation,mutate):
        class Tx:
            def run(self,sql,params):
                m=MagicMock()
                answer={"task_id":params["task_id"],"claim_id":params["claim_id"],
                        "session_id":params["session_id"],"attempt":params["attempt"],
                        "status":"DONE","completed_by":"hermes-local"}
                answer.update(wrong)
                m.single.return_value=answer
                return m
        assert mutate(Tx()) is False
        raise g.GenerationProtocolError("task_mutation_not_acknowledged")
    monkeypatch.setattr(c,"advance_candidate_generation",simulate)
    with pytest.raises(g.GenerationProtocolError):
        c.complete_candidate_claim(
            None,expected_generation=3,task_id="task1",claim_id="claim1",
            session_id="session1",attempt=1,
        )


def test_lease_checks_neo4j_time_not_caller_clock():
    assert "coalesce(t.lease_expires_at_ts,0) > timestamp()" in c._COMPLETE
    assert "$now_ms" not in c._COMPLETE
    import inspect
    assert "now_ms" not in inspect.signature(c.complete_candidate_claim).parameters


def test_existing_production_code_does_not_import_research_adapter():
    root=Path(__file__).resolve().parents[1]/"src/assistx"
    # Excluding research prototype from this negative audit.
    for path in root.rglob("*.py"):
        if path.name.startswith("hermes_source_") or path.name == "hermes_broker_command_research.py":
            continue
        text=path.read_text(encoding="utf-8")
        assert "hermes_source_completion_research" not in text, path
