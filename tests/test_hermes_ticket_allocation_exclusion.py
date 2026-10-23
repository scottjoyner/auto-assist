"""Source contract and mock receipts for unfenced ticket / allocation writers.

These guards are necessary but DO NOT establish all-writer generation
serialization, global authorization or production release acceptance.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from test_hermes_claim_admission_fencing import ClaimAdmissionTests

SRC=Path(__file__).resolve().parents[1]/"src/assistx/neo4j_client.py"
FIELDS=("claimed_by","target_agent_id","completed_by","heartbeat_by",
        "failed_by","paused_from_agent_id","migration_target_agent_id",
        "passive_claim_agent_id","last_passive_claim_agent_id")


def queries(name):
    tree=ast.parse(SRC.read_text())
    klass=next(n for n in tree.body if isinstance(n,ast.ClassDef)
               and n.name=="Neo4jClient")
    fn=next(n for n in klass.body if isinstance(n,ast.FunctionDef)
            and n.name==name)
    return [n.args[0].value for n in ast.walk(fn)
            if isinstance(n,ast.Call)
            and getattr(n.func,"attr","")=="run"
            and n.args and isinstance(n.args[0],ast.Constant)
            and isinstance(n.args[0].value,str)]


@pytest.mark.parametrize("method,fragment,mutation",[
    ("upsert_ticket","idempotency_key:$idempotency_key","SET t += $props"),
    ("reserve_task_allocation","MATCH (n:SwarmNode","SET old.status='SUPERSEDED'"),
    ("release_task_allocation","id:$reservation_id, status:'ACTIVE'","SET a.status='RELEASED'"),
])
def test_all_paths_lock_and_failclosed_before_change(method,fragment,mutation):
    sql=next(x for x in queries(method) if fragment in x)
    assert "SET t.hermes_fence_check_epoch =" in sql
    assert "WITH t" in sql
    assert "AND NOT (" in sql
    lock=sql.index("SET t.hermes_fence_check_epoch =")
    exclusion=sql.index("AND NOT (",lock)
    change=sql.index(mutation,lock)
    assert lock<exclusion<change
    for field in FIELDS:
        pred=f"coalesce(t.{field}, '') STARTS WITH 'hermes-'"
        assert pred in sql
        assert pred not in sql.replace(pred,"false",1)


def test_ticket_retry_requires_unclaimed_and_no_legacy_generation():
    sql=next(x for x in queries("upsert_ticket") if "idempotency_key:$idempotency_key" in x)
    assert "t.claim_id IS NULL" in sql
    assert "coalesce(t.execution_attempt,0)=0" in sql
    assert "t.status NOT IN ['CLAIMED','CLAIMED_PASSIVE','RUNNING'," in sql
    assert "t.hermes_ticket_creation_nonce=$id AS created_now" in sql


def test_allocation_admission_requires_unclaimed_ready_and_nonhermes_caller():
    sql=queries("reserve_task_allocation")[0]
    for pred in ("t.status='READY'","t.claim_id IS NULL",
                 "coalesce(t.execution_attempt,0)=0",
                 "NOT ($node_id STARTS WITH 'hermes-')",
                 "NOT ($actor STARTS WITH 'hermes-')"):
        assert pred in sql
    assert sql.index("SET t.hermes_fence_check_epoch") < sql.index("WHERE t.status='READY'")
    assert sql.index("WHERE t.status='READY'") < sql.index("CREATE (a:AllocationReservation")


def test_release_guards_exact_reservation_and_status():
    sql=queries("release_task_allocation")[0]
    assert "t.allocation_reservation_id=$reservation_id" in sql
    assert "t.status='READY'" in sql
    assert "t.claim_id IS NULL" in sql
    assert sql.index("SET t.hermes_fence_check_epoch") < sql.index("WHERE t.status='READY'")
    assert sql.index("WHERE t.status='READY'") < sql.index("SET a.status='RELEASED'")


def test_denied_idempotent_ticket_never_returns_success():
    case=ClaimAdmissionTests()
    case.Neo=ClaimAdmissionTests.Neo
    cli,db=case.client([None])
    with pytest.raises(ValueError,match="Task ticket upsert rejected"):
        cli.upsert_ticket(title="synthetic",idempotency_key="synthetic")
    assert len(db.queries)==1
    assert "SET t.hermes_fence_check_epoch" in db.queries[0][0]


def test_eligible_idempotent_ticket_acks_current_task():
    case=ClaimAdmissionTests()
    case.Neo=ClaimAdmissionTests.Neo
    cli,db=case.client([{"id":"synthetic-task"}])
    assert cli.upsert_ticket(title="synthetic",idempotency_key="synthetic")=="synthetic-task"
    assert db.queries[0][1]["props"]["title"]=="synthetic"


@pytest.mark.parametrize("method,args,expected",[
    ("reserve_task_allocation",
     {"task_id":"synthetic","node_id":"node-a","model_id":None,
      "snapshot_revision":1,"actor":"synthetic"}, "reserved"),
    ("release_task_allocation",{"reservation_id":"synthetic","actor":"synthetic"},"released"),
])
def test_denied_allocation_does_not_claim_success(method,args,expected):
    case=ClaimAdmissionTests()
    case.Neo=ClaimAdmissionTests.Neo
    cli,db=case.client([None])
    output=getattr(cli,method)(**args)
    assert output[expected] is False
    assert db.queries[0][0].index("SET t.hermes_fence_check_epoch") < db.queries[0][0].index("WHERE ")
