"""Task batch retries may not revive active/terminal Hermes-associated work."""
from __future__ import annotations

import ast
from pathlib import Path

from test_hermes_claim_admission_fencing import ClaimAdmissionTests

SRC=Path(__file__).resolve().parents[1]/"src/assistx/neo4j_client.py"
FIELDS=(
    "claimed_by","target_agent_id","completed_by","heartbeat_by",
    "failed_by","paused_from_agent_id","migration_target_agent_id",
    "passive_claim_agent_id","last_passive_claim_agent_id",
)

def sql():
    tree=ast.parse(SRC.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef)
             and n.name=="Neo4jClient")
    fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef)
            and n.name=="create_tasks_batch")
    calls=[n.args[0].value for n in ast.walk(fn)
           if isinstance(n,ast.Call)
           and getattr(n.func,"attr","")=="run"
           and n.args and isinstance(n.args[0],ast.Constant)
           and isinstance(n.args[0].value,str)]
    return next(x for x in calls if "idempotency_key:$ik" in x)

def test_batch_idempotent_task_lock_and_state_guard():
    stmt=sql()
    assert "SET t.hermes_fence_check_epoch =" in stmt
    assert "WITH t" in stmt
    assert "t.status IS NULL OR t.status IN ['READY','FAILED']" in stmt
    assert stmt.index("SET t.hermes_fence_check_epoch =") < stmt.index("WITH t")
    assert stmt.index("WITH t") < stmt.index("WHERE (t.status IS NULL")
    assert stmt.index("WHERE (t.status IS NULL") < stmt.index("SET t += $props")
    assert "t.status=$st" in stmt
    for field in FIELDS:
        needle=f"coalesce(t.{field}, '') STARTS WITH 'hermes-'"
        assert needle in stmt
        assert needle not in stmt.replace(needle,"false",1)

def test_batch_declined_task_does_not_inflate_written_count():
    fixture=ClaimAdmissionTests()
    fixture.Neo=ClaimAdmissionTests.Neo
    client,session=fixture.client([None])
    out=client.create_tasks_batch([{
        "idempotency_key":"synthetic-old","title":"synthetic",
        "kind":"task",
    }])
    assert out==0
    assert len(session.queries)==1
    assert "WHERE (t.status IS NULL" in session.queries[0][0]

def test_batch_genuine_nonhermes_task_counts_ack():
    fixture=ClaimAdmissionTests()
    fixture.Neo=ClaimAdmissionTests.Neo
    client,session=fixture.client([{"id":"synthetic-task"}])
    assert client.create_tasks_batch([
        {"idempotency_key":"synthetic","title":"task"},
    ])==1
    assert session.queries[0][1]["st"]=="READY"
