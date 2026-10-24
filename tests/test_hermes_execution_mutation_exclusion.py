"""Fail-closed guards for generic checkpoint/preemption/migration writers.

Synthetic source and mock checks, not proof of resource-owned source fencing.
"""
from __future__ import annotations

import ast
from pathlib import Path
import pytest
from assistx.execution_control import ExecutionControlPlane

SRC=Path(__file__).resolve().parents[1]/"src/assistx/execution_control.py"
FIELDS=("claimed_by","target_agent_id","completed_by",
        "paused_from_agent_id","migration_target_agent_id")

class NeverNeo:
    def _session(self):
        raise AssertionError("denied action must not open a DB session")


@pytest.mark.parametrize("agent",["hermes-local","hermes-other"])
def test_hermes_checkpoint_denied_before_database(agent):
    result=ExecutionControlPlane().checkpoint(
        NeverNeo(),"synthetic-task",agent,"synthetic-claim",
        checkpoint={"cursor":2},progress=0.5,
        estimated_remaining_seconds=None,pause=True)
    assert result=={"checkpointed":False,
                    "reason":"hermes_source_generation_fence_required"}


@pytest.mark.parametrize("actor,target",[
    ("hermes-local","node-1"),("operator","hermes-other")
])
def test_hermes_preemption_denied_before_database(actor,target):
    result=ExecutionControlPlane().request_preemption(
        NeverNeo(),"synthetic-task",actor,
        reason="synthetic",target_agent_id=target)
    assert result=={"requested":False,
                    "reason":"hermes_source_generation_fence_required"}


@pytest.mark.parametrize("actor,target",[
    ("hermes-local","node-1"),("operator","hermes-other")
])
def test_hermes_migration_denied_before_database(actor,target):
    result=ExecutionControlPlane().migrate(
        NeverNeo(),"synthetic-task",target,actor)
    assert result=={"migrated":False,
                    "reason":"hermes_source_generation_fence_required"}


def _queries():
    tree=ast.parse(SRC.read_text())
    cls=next(x for x in tree.body if isinstance(x,ast.ClassDef)
             and x.name=="ExecutionControlPlane")
    out={}
    for fn in cls.body:
        if not isinstance(fn,ast.FunctionDef) or fn.name not in (
                "checkpoint","request_preemption","migrate"):
            continue
        matches=[n.args[0].value for n in ast.walk(fn)
                 if isinstance(n,ast.Call)
                 and getattr(n.func,"attr","")=="run"
                 and n.args and isinstance(n.args[0],ast.Constant)
                 and isinstance(n.args[0].value,str)]
        assert len(matches)==1, fn.name
        out[fn.name]=matches[0]
    return out


@pytest.mark.parametrize("fn,write",[
    ("checkpoint","CREATE (checkpoint:TaskCheckpoint {"),
    ("request_preemption","SET t.status='PAUSING'"),
    ("migrate","SET t.status='READY'"),
])
def test_atomic_lock_and_full_owner_scope_before_effect(fn,write):
    sql=_queries()[fn]
    prefix=sql.split(write,1)[0]
    assert prefix.count("SET t.hermes_fence_check_epoch =")==1
    assert "WITH t" in prefix
    assert "AND NOT (" in prefix
    assert prefix.index("SET t.hermes_fence_check_epoch =") < prefix.index("AND NOT (")
    for field in FIELDS:
        predicate=f"coalesce(t.{field}, '') STARTS WITH 'hermes-'"
        assert predicate in prefix
        changed=prefix.replace(predicate,"false",1)
        assert predicate not in changed


def test_migration_rechecks_status_after_task_lock():
    sql=_queries()["migrate"]
    assert sql.index("SET t.hermes_fence_check_epoch =") < sql.index("WHERE t.status='PAUSED'")
    assert sql.index("WHERE t.status='PAUSED'") < sql.index("SET t.status='READY'")
