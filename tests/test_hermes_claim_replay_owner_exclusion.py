"""Hermes claim admission requires current identity and denies legacy bypasses."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from test_hermes_claim_admission_fencing import ClaimAdmissionTests

SRC=Path(__file__).resolve().parents[1]/"src/assistx/neo4j_client.py"
FIELDS=(
    "claimed_by","target_agent_id","completed_by","heartbeat_by",
    "failed_by","paused_from_agent_id","migration_target_agent_id",
    "passive_claim_agent_id","last_passive_claim_agent_id",
)

def claim_queries():
    tree=ast.parse(SRC.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef)
             and n.name=="Neo4jClient")
    fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef)
            and n.name=="claim_task")
    return [n.args[0].value for n in ast.walk(fn)
            if isinstance(n,ast.Call)
            and getattr(n.func,"attr","")=="run"
            and n.args and isinstance(n.args[0],ast.Constant)
            and isinstance(n.args[0].value,str)]

def test_all_replay_and_new_claims_lock_before_owner_test():
    q=claim_queries()
    match=[sql for sql in q if "SET t.hermes_fence_check_epoch" in sql]
    assert len(match)==3
    for sql in match:
        assert sql.index("SET t.hermes_fence_check_epoch") < sql.index("WITH t")
        assert sql.index("WITH t") < sql.index("WHERE ")
    for field in FIELDS:
        fragment=f"coalesce(t.{field}, '') STARTS WITH 'hermes-'"
        assert any(fragment in x for x in match)
    assert "t.claimed_by=$agent_id" in match[1] or any(
        "t.claimed_by=$agent_id" in sql for sql in match)

def test_legacy_replay_query_denies_hermes_lineage():
    queries=claim_queries()
    legacy=next(q for q in queries if "($session_id IS NULL OR t.agent_session_id=$session_id)" in q)
    assert "t.claimed_by=$agent_id" in legacy
    assert "t.claim_id=$claim_id" in legacy
    assert "t.status IN ['CLAIMED','RUNNING','PAUSING']" in legacy
    for field in FIELDS[1:]:
        x=f"coalesce(t.{field}, '') STARTS WITH 'hermes-'"
        assert x in legacy
    for field in FIELDS[1:]:
        pred=f"coalesce(t.{field}, '') STARTS WITH 'hermes-'"
        modified=legacy.replace(pred,"false",1)
        assert pred not in modified

def test_ready_claim_allows_only_fresh_exact_hermes_target():
    sql=next(q for q in claim_queries() if "OPTIONAL MATCH (t)-[:HAS_ALLOCATION]" in q)
    for clause in (
        "SET t.hermes_fence_check_epoch",
        "t.status='READY'",
        "AND t.target_agent_id=$agent_id",
        "coalesce(t.execution_attempt, 0)=0",
        "t.claim_id IS NULL",
    ):
        assert clause in sql
    assert sql.index("SET t.hermes_fence_check_epoch") < sql.index("WHERE t.status='READY'")
    for field in FIELDS:
        assert "coalesce(t."+field+", '') STARTS WITH 'hermes-'" in sql

def test_prefix_hermes_identity_must_have_session_before_db():
    fixture=ClaimAdmissionTests()
    fixture.Neo=ClaimAdmissionTests.Neo
    for agent in ("hermes-local","hermes-disposable","hermes-other"):
        cli,session=fixture.client()
        assert not cli.claim_task("synthetic",agent,session_id=None)["claimed"]
        assert session.queries==[]

def test_replay_uses_exact_agent_in_session_parameters():
    fixture=ClaimAdmissionTests()
    fixture.Neo=ClaimAdmissionTests.Neo
    for agent in ("hermes-disposable","legacy-worker"):
        cli,session=fixture.client([None,None,{
            "status":"DONE","target_agent_id":None,
            "required_capabilities":[],"capabilities":[],
            "paperclip_dispatched":False}])
        assert not cli.claim_task(
            "synthetic",agent,session_id="sess-1",
            idempotency_key="old",
        )["claimed"]
        assert session.queries[0][1]["agent_id"]==agent
