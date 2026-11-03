"""Isolated source contracts; no Neo4j or service import required."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from assistx import legacy_task_maintenance as maintenance


class Record:
    def __init__(self, value):
        self.value = value

    def single(self):
        return self.value


class Tx:
    def __init__(self, rows=1, affected=3):
        self.rows = rows
        self.affected = affected
        self.calls = []

    def run(self, query, params=None):
        self.calls.append((query, params))
        if len(self.calls) == 1:
            return Record({"matching_rows": self.rows})
        return Record({"affected": self.affected})


class Session:
    def __init__(self, tx):
        self.tx = tx

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute_write(self, fn, *args):
        return fn(self.tx, *args)


class Neo:
    def __init__(self, tx):
        self.tx = tx

    def _session(self):
        return Session(self.tx)


@pytest.mark.parametrize("operation,args,keyword", [
    (maintenance.reap_stale_legacy_tasks, {"lease_seconds": 600}, "status='READY'"),
    (maintenance.retain_terminal_legacy_tasks, {"retention_days": 30}, "DETACH DELETE"),
])
def test_separate_post_lock_query_and_protection_guards(operation, args, keyword):
    tx = Tx()
    assert operation(Neo(tx), **args) == 3
    assert len(tx.calls) == 2
    assert "MATCH (w:WorkSupplyState {name:'canonical'})" in tx.calls[0][0]
    assert "legacy_maintenance_serial" in tx.calls[0][0]
    query = tx.calls[1][0]
    assert keyword in query
    for guard in [
        "t.machine_authorization_generation IS NULL",
        "t.machine_authorization_approval_id IS NULL",
        "t.machine_token_epoch IS NULL",
        "t.target_agent_id IS NULL",
        "coalesce(t.hermes_claim_scope,false)=false",
        "t.projection_generation IS NULL",
    ]:
        assert guard in query
    assert "WITH DISTINCT t" in query


@pytest.mark.parametrize("rows", [0, 2, 3])
@pytest.mark.parametrize("operation,args", [
    (maintenance.reap_stale_legacy_tasks, {"lease_seconds": 600}),
    (maintenance.retain_terminal_legacy_tasks, {"retention_days": 30}),
    (maintenance.retain_unlinked_legacy_memory_items, {"retention_days": 30}),
])
def test_missing_or_duplicate_lock_never_runs_task_write(rows, operation, args):
    tx = Tx(rows=rows)
    with pytest.raises(maintenance.MaintenanceTaskWriteBlocked, match="not_unique"):
        operation(Neo(tx), **args)
    assert len(tx.calls) == 1


@pytest.mark.parametrize("lease", [0, -1, 86401, 1.2, True, None])
def test_bad_reaper_lease_never_enters_transaction(lease):
    tx = Tx()
    with pytest.raises(maintenance.MaintenanceTaskWriteBlocked):
        maintenance.reap_stale_legacy_tasks(Neo(tx), lease_seconds=lease)
    assert not tx.calls


@pytest.mark.parametrize("days", [0, -1, 36501, 1.5, True, None])
def test_bad_retention_days_never_enters_transaction(days):
    tx = Tx()
    with pytest.raises(maintenance.MaintenanceTaskWriteBlocked):
        maintenance.retain_terminal_legacy_tasks(Neo(tx), retention_days=days)
    assert not tx.calls


def test_all_three_maintenance_writers_use_guarded_helper():
    path = Path(__file__).resolve().parents[1] / "src" / "assistx" / "maintenance.py"
    tree = ast.parse(path.read_text())
    fn = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in ("maintenance_job", "_loop"):
        node = fn[name]
        calls = {n.func.id for n in ast.walk(node)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "reap_stale_legacy_tasks" in calls
        assert not any(isinstance(n, ast.Attribute) and n.attr == "run"
                       and any("Task" in ast.unparse(a) for a in n.args)
                       for n in ast.walk(node) if isinstance(n, ast.Call))
    assert "retain_terminal_legacy_tasks" in {
        n.func.id for n in ast.walk(fn["maintenance_job"])
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "retain_unlinked_legacy_memory_items" in {
        n.func.id for n in ast.walk(fn["maintenance_job"])
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def test_scheduled_maintenance_uses_both_guarded_writers(monkeypatch):
    from assistx import maintenance as scheduler
    from assistx import neo4j_client

    calls = []
    class MemoryRow:
        def single(self):
            return {"deleted": 4}
    class MemorySession:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def run(self, query, *_):
            assert "MATCH (m:MemoryItem)" in query
            calls.append("memory")
            return MemoryRow()
    class FakeNeo:
        def _session(self):
            return MemorySession()
        def close(self):
            calls.append("closed")
    monkeypatch.setattr(neo4j_client, "Neo4jClient", FakeNeo)
    monkeypatch.setattr(
        maintenance, "reap_stale_legacy_tasks",
        lambda neo, *, lease_seconds: calls.append("reap") or 2,
    )
    monkeypatch.setattr(
        maintenance, "retain_terminal_legacy_tasks",
        lambda neo, *, retention_days: calls.append("retain") or 3,
    )
    monkeypatch.setattr(
        maintenance, "retain_unlinked_legacy_memory_items",
        lambda neo, *, retention_days: calls.append("memory") or 4,
    )
    monkeypatch.setattr(scheduler.answers_store, "rebuild_index",
                        lambda: {"reindexed": 5})
    monkeypatch.setattr(scheduler, "_reschedule", lambda: calls.append("rescheduled"))
    result = scheduler.maintenance_job()
    assert result == {
        "tasks_deleted": 3,
        "memory_deleted": 4,
        "answers_reindexed": 5,
        "stale_claims_reaped": 2,
    }
    assert calls == ["reap", "retain", "memory", "closed", "rescheduled"]


def test_maintenance_missing_graph_lock_does_not_fallback_to_task_writes(monkeypatch):
    from assistx import maintenance as scheduler
    from assistx import neo4j_client

    calls = []
    class MemoryRow:
        def single(self):
            return {"deleted": 1}
    class MemorySession:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def run(self, query, *_):
            assert "MATCH (m:MemoryItem)" in query
            calls.append("memory")
            return MemoryRow()
    class FakeNeo:
        def _session(self):
            return MemorySession()
        def close(self):
            calls.append("closed")
    def denied(*args, **kwargs):
        calls.append("blocked")
        raise maintenance.MaintenanceTaskWriteBlocked("canonical lock absent")
    monkeypatch.setattr(neo4j_client, "Neo4jClient", FakeNeo)
    monkeypatch.setattr(maintenance, "reap_stale_legacy_tasks", denied)
    monkeypatch.setattr(maintenance, "retain_terminal_legacy_tasks", denied)
    monkeypatch.setattr(maintenance, "retain_unlinked_legacy_memory_items", denied)
    monkeypatch.setattr(scheduler.answers_store, "rebuild_index",
                        lambda: {"reindexed": 0})
    monkeypatch.setattr(scheduler, "_reschedule", lambda: calls.append("rescheduled"))
    result = scheduler.maintenance_job()
    assert result["tasks_deleted"] == 0
    assert result["stale_claims_reaped"] == 0
    assert result["memory_deleted"] == 0
    assert calls == ["blocked", "blocked", "blocked", "closed", "rescheduled"]



def test_task_linked_memory_retention_query_is_post_lock_and_fail_closed():
    tx=Tx()
    assert maintenance.retain_unlinked_legacy_memory_items(
        Neo(tx), retention_days=30,
    )==3
    assert len(tx.calls)==2
    assert "MATCH (w:WorkSupplyState {name:'canonical'})" in tx.calls[0][0]
    query=tx.calls[1][0]
    assert "MATCH (m:MemoryItem)" in query
    assert "NOT EXISTS" in query
    assert "MATCH (:Task)-[:RELATED_MEMORY]->(m)" in query
    assert "DETACH DELETE m" in query
    assert "WITH DISTINCT m" in query


@pytest.mark.parametrize("days",[0,-1,36501,1.2,True,None])
def test_invalid_memory_retention_does_not_open_transaction(days):
    tx=Tx()
    with pytest.raises(maintenance.MaintenanceTaskWriteBlocked):
        maintenance.retain_unlinked_legacy_memory_items(
            Neo(tx),retention_days=days,
        )
    assert not tx.calls
