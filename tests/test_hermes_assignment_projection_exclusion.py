"""Generic assignment projections may not overwrite a Hermes source claim.

This is a local SQL contract and mock-based test, not an independently
fenced owner generation or a production-readiness assertion.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from assistx.swarm_core import project_assignment_event
import assistx.swarm_core as core

SRC = Path(core.__file__)


class Neo:
    def __init__(self):
        self.session = MagicMock()
        self.context = MagicMock()
        self.context.__enter__.return_value = self.session
        self.context.__exit__.return_value = False
    def _session(self):
        return self.context


def query():
    tree=ast.parse(SRC.read_text(encoding="utf-8"))
    fn=next(n for n in tree.body if isinstance(n, ast.FunctionDef)
            and n.name=="project_assignment_event")
    runs=[n for n in ast.walk(fn) if isinstance(n, ast.Call)
          and getattr(n.func,"attr","")=="run"
          and n.args and isinstance(n.args[0],ast.Constant)]
    assert len(runs)==1
    return runs[0].args[0].value


@pytest.mark.parametrize("field",[
    "worker_id","agent_id","claimed_by","target_agent_id"
])
@pytest.mark.parametrize("event_type",[
    "assignment.claimed","assignment.heartbeat","assignment.completed",
    "assignment.failed","assignment.released","assignment.expired"
])
def test_hermes_payload_never_opens_generic_db_session(field,event_type):
    n=Neo()
    project_assignment_event(n,
                             {"event_id":"synthetic-event","event_type":event_type},
                             {"task_id":"synthetic-task",field:"hermes-local"})
    n.context.__enter__.assert_not_called()


def test_nested_actor_and_event_identity_denied():
    for event in (
        {"event_id":"e","event_type":"assignment.claimed","agent_id":"hermes-local"},
        {"event_id":"e","event_type":"assignment.claimed",
         "actor":{"agent_id":"hermes-local"}},
    ):
        n=Neo()
        project_assignment_event(n,event,{"task_id":"synthetic-task"})
        n.context.__enter__.assert_not_called()


def test_unrelated_assignment_remains_projectable():
    n=Neo()
    project_assignment_event(
        n,{"event_id":"synthetic-event","event_type":"assignment.claimed"},
        {"task_id":"synthetic-task","worker_id":"worker-other",
         "assignment_id":"synthetic-assignment"},
    )
    n.session.run.assert_called_once()
    sql, args=n.session.run.call_args.args
    assert args["worker_id"]=="worker-other"
    assert args["task_id"]=="synthetic-task"
    assert sql==query()


def test_source_task_lock_precedes_owner_qualifier_and_mutation():
    sql=query()
    assert sql.count("MERGE (t:Task {id:$task_id})")==1
    assert sql.count("SET t.hermes_fence_check_epoch =")==1
    assert "WITH e, t" in sql
    prefix=sql.split("SET t.updated_at=datetime()",1)[0]
    assert prefix.index("SET t.hermes_fence_check_epoch =") < prefix.index("WHERE NOT (")
    fields=[
        "claimed_by","target_agent_id","completed_by","heartbeat_by",
        "failed_by","paused_from_agent_id","migration_target_agent_id"
    ]
    for name in fields:
        assert "coalesce(t."+name+", '') STARTS WITH 'hermes-'" in prefix
    for name in fields:
        corrupt=prefix.replace(
            "coalesce(t."+name+", '') STARTS WITH 'hermes-'",
            "false",1)
        assert "coalesce(t."+name+", '') STARTS WITH 'hermes-'" not in corrupt


def test_unknown_origin_cannot_be_claimed_as_authoritative():
    # The protective no-op must not be confused with complete provenance.
    sql=query()
    assert "t.status=$status" in sql
    assert "WHERE NOT (" in sql
    assert "source_generation" not in sql

def test_event_receipt_does_not_claim_projection_on_hermes_denial(monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setattr(core, "validate_event_envelope", lambda event: None)
    monkeypatch.setattr(core, "build_event_metadata", lambda event, metadata: {})
    monkeypatch.setattr(core, "upsert_artifact_refs", lambda neo, refs: [])
    neo=Neo()
    neo.session.run.return_value.single.return_value=None
    event={
        "event_id":"synthetic-event",
        "idempotency_key":"synthetic-key",
        "event_type":"assignment.claimed",
        "payload":{"task_id":"synthetic-task","worker_id":"hermes-local"},
        "subject":{"kind":"task","id":"synthetic-task"},
        "artifact_refs":[],
        "privacy":{},
        "source_repo":"test-repo",
        "source_service":"test-service",
        "node_id":"test-node",
        "occurred_at":"2026-10-23T13:00:00Z",
        "schema_version":"1.0",
    }
    receipt=core.record_event(neo,event)
    assert receipt["accepted"]
    assert not receipt["deduped"]
    assert receipt["graph_reconciled"] is False
    assert neo.session.run.call_count==2
    assert "MERGE (t:Task" not in str(neo.session.run.call_args_list)


def test_projection_reports_false_when_stored_task_owner_denies():
    neo=Neo()
    neo.session.run.return_value.single.return_value=None
    event={"event_id":"synthetic-event","event_type":"assignment.heartbeat"}
    result=project_assignment_event(neo,event,{
        "task_id":"synthetic-task","worker_id":"nonhermes-worker"
    })
    assert result is False
    assert neo.session.run.call_count==1
