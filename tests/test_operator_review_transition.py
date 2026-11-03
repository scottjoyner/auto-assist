"""Static and synthetic research tests; no graph/principal required."""
from __future__ import annotations

from pathlib import Path

import pytest

from assistx.operator_review_transition import (
    ReviewTransitionDenied, _in_tx, propose_operator_review_transition,
)


class Row:
    def __init__(self, value):
        self.value=value
    def single(self):
        return self.value


class Tx:
    def __init__(self, rows=1, approved=True):
        self.rows=rows
        self.approved=approved
        self.calls=[]
    def run(self, query, parameters=None):
        self.calls.append((query,parameters))
        if len(self.calls)==1:
            return Row({"matching_rows":self.rows})
        return Row({
            "task_id":"synthetic-review",
            "status":"READY",
            "approved_by":"synthetic-operator",
            "updated_at_ts":9876543,
            "review_version":1,
        } if self.approved else None)


class Session:
    def __init__(self, tx):
        self.tx=tx
    def __enter__(self):
        return self
    def __exit__(self,*_):
        return False
    def execute_write(self,func,*args):
        return func(self.tx,*args)


class Neo:
    def __init__(self, tx):
        self.tx=tx
        self.sessions=0
    def _session(self):
        self.sessions+=1
        return Session(self.tx)


def call(neo,**kwargs):
    params=dict(task_id="synthetic-review",actor="synthetic-operator",
        expected_status="REVIEW",expected_updated_at_ts=1234567)
    params.update(kwargs)
    return propose_operator_review_transition(neo,**params)


def test_shared_lock_first_then_exact_revision_status_and_provenance():
    tx=Tx()
    assert call(Neo(tx))["status"]=="READY"
    assert len(tx.calls)==2
    assert "WorkSupplyState {name:'canonical'}" in tx.calls[0][0]
    assert "operator_review_serial" in tx.calls[0][0]
    query=tx.calls[1][0]
    for guard in (
        "collect(t0) AS matched","size(matched)=1",
        "t.status=$expected_status",
        "t.updated_at_ts=$expected_updated_at_ts",
        "t.status IN ['REVIEW','PROPOSED']",
        "coalesce(t.kind,'') <> 'intent_review'",
        "t.approved_by IS NULL","t.review_decision IS NULL",
        "t.claim_id IS NULL","t.claimed_by IS NULL",
        "coalesce(t.execution_attempt,0)=0",
        "t.target_agent_id IS NULL",
        "t.machine_authorization_generation IS NULL",
        "t.machine_authorization_approval_id IS NULL",
        "t.machine_token_epoch IS NULL",
        "t.projection_generation IS NULL",
        "t.hermes_claim_scope,false)=false",
        "MATCH (t)-[:HAS_ALLOCATION]->(:AllocationReservation)",
        "MATCH (t)-[:DISPATCHED_AS]->(:Dispatch)",
        "t.review_version=coalesce(t.review_version,0)+1",
    ):
        assert guard in query
    assert tx.calls[1][1]=={
        "task_id":"synthetic-review",
        "actor":"synthetic-operator",
        "expected_status":"REVIEW",
        "expected_updated_at_ts":1234567,
    }


@pytest.mark.parametrize("rows",[0,2,3])
def test_zero_or_duplicate_lock_rolls_back_before_task_update(rows):
    tx=Tx(rows=rows)
    with pytest.raises(ReviewTransitionDenied,match="lock_missing_or_duplicate"):
        call(Neo(tx))
    assert len(tx.calls)==1


def test_stale_or_protected_review_fails_closed():
    tx=Tx(approved=False)
    with pytest.raises(ReviewTransitionDenied,match="stale_or_protected"):
        call(Neo(tx))
    assert len(tx.calls)==2


@pytest.mark.parametrize("params",[
    {"task_id":""},{"task_id":None},{"task_id":"x"*257},
    {"actor":""},{"actor":None},{"actor":"x"*129},
    {"expected_status":"DONE"},{"expected_status":"READY"},
    {"expected_status":"CANCELLED"},{"expected_status":None},
    {"expected_updated_at_ts":0},{"expected_updated_at_ts":-1},
    {"expected_updated_at_ts":None},{"expected_updated_at_ts":True},
    {"expected_updated_at_ts":1234.0},
])
def test_bad_request_refused_without_database(params):
    neo=Neo(Tx())
    with pytest.raises(ReviewTransitionDenied,match="invalid_operator"):
        call(neo,**params)
    assert neo.sessions==0


def test_different_review_workflows_explicitly_supported():
    tx=Tx()
    assert call(Neo(tx),expected_status="PROPOSED")["status"]=="READY"
    assert tx.calls[1][1]["expected_status"]=="PROPOSED"


def test_never_routed_by_default_no_implicit_principal():
    # No production API, CLI or job caller is added by this source-only patch.
    root=Path(__file__).resolve().parents[1]
    for name in ("src/assistx/api.py","src/assistx/cli.py",
                 "src/assistx/jobs.py","src/assistx/pipeline_execute.py",
                 "src/assistx/routers/review.py"):
        text=(root/name).read_text()
        assert "propose_operator_review_transition" not in text
