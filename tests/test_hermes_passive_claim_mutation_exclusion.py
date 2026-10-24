"""Static and mock checks for all five passive Task writers.

This does not prove independent owner-generation fencing or resource effects.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock
import pytest
import assistx.passive_claims as passive

FILE=Path(passive.__file__)
WRITERS=("_tx_claim_passive_task", "_tx_renew_passive_claim",
         "_tx_release_passive_claim", "_tx_expire_passive_claims",
         "_rollback_claim")
FIELDS=("claimed_by","target_agent_id","completed_by","heartbeat_by",
        "failed_by","paused_from_agent_id","migration_target_agent_id",
        "passive_claim_agent_id","last_passive_claim_agent_id")


def query(name):
    tree=ast.parse(FILE.read_text(encoding="utf-8"))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
            and n.name==name)
    calls=[n for n in ast.walk(fn)
           if isinstance(n,ast.Call) and getattr(n.func,"attr","")=="run"
           and n.args and isinstance(n.args[0],ast.Constant)
           and isinstance(n.args[0].value,str)]
    assert len(calls)==1,name
    return calls[0].args[0].value


@pytest.mark.parametrize("name",WRITERS)
def test_all_five_passive_writers_lock_and_exclude_hermes(name):
    sql=query(name)
    assert "MATCH (t:Task)" in sql
    assert sql.count("SET t.hermes_fence_check_epoch =")==1
    assert "WITH t" in sql and "AND NOT (" in sql
    for field in FIELDS:
        predicate=f"coalesce(t.{field}, '') STARTS WITH 'hermes-'"
        assert predicate in sql
        tampered=sql.replace(predicate,"false",1)
        assert predicate not in tampered
    lock=sql.index("SET t.hermes_fence_check_epoch =")
    guard=sql.index("AND NOT (",lock)
    if name=="_tx_claim_passive_task":
        mutation=sql.index("SET t.previous_status = t.status",lock)
    elif name=="_tx_renew_passive_claim":
        mutation=sql.index("SET t.passive_claim_expires_at_ts = $expires_at_ts",lock)
    elif name=="_tx_release_passive_claim":
        mutation=sql.index("SET t.status = $next_status",lock)
    elif name=="_tx_expire_passive_claims":
        mutation=sql.index("SET t.status = next_status",lock)
    else:
        mutation=sql.index("SET t.status = coalesce(t.previous_status",lock)
    assert lock<guard<mutation


@pytest.mark.parametrize("name",WRITERS[:3])
def test_claim_renew_release_prevent_hermes_agent_tokens(name):
    sql=query(name)
    assert "AND NOT (coalesce($agent_id, '') STARTS WITH 'hermes-')" in sql
    assert sql.index("WHERE coalesce(t.id, t.task_id) = $task_id") < sql.index(
        "SET t.hermes_fence_check_epoch =")


def test_expiry_rechecks_candidate_after_limit_and_write_lock():
    sql=query("_tx_expire_passive_claims")
    lock=sql.index("SET t.hermes_fence_check_epoch =")
    assert sql.index("LIMIT $limit")<lock
    assert lock<sql.index(
        "WHERE toUpper(coalesce(t.status, '')) = 'CLAIMED_PASSIVE'",lock)


def _fake_neodb(ack):
    session=MagicMock()
    session.run.return_value.single.return_value=ack
    context=MagicMock()
    context.__enter__.return_value=session
    db=MagicMock()
    db.driver.session.return_value=context
    return db,session


def test_rollback_denial_does_not_emit_false_audit_receipt(monkeypatch):
    db,session=_fake_neodb(None)
    emitted=[]
    monkeypatch.setattr(passive,"record_passive_event",
                        lambda *a,**k: emitted.append((a,k)))
    passive._rollback_claim(lambda:db,"synthetic","synthetic-claim","safety_rejected")
    session.run.assert_called_once()
    assert "RETURN coalesce(t.id, t.task_id) AS rolled_back_task_id" in session.run.call_args.args[0]
    assert emitted==[]
    db.close.assert_called_once()


def test_rollback_ack_emits_one_event(monkeypatch):
    db,session=_fake_neodb({"rolled_back_task_id":"synthetic"})
    emitted=[]
    monkeypatch.setattr(passive,"record_passive_event",
                        lambda *a,**k: emitted.append((a,k)))
    passive._rollback_claim(lambda:db,"synthetic","synthetic-claim","safety_rejected")
    assert len(emitted)==1
    assert emitted[0][0][1]=="passive_claim.rollback"
