"""Isolated source-contract tests; no graph, credentials or running services."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from assistx import legacy_intent_cancel as cancellation


class Row:
    def __init__(self, data):
        self.data = data

    def single(self):
        return self.data


class Tx:
    def __init__(self, count=1, cancelled=2):
        self.count = count
        self.cancelled = cancelled
        self.calls = []

    def run(self, query, params=None):
        self.calls.append((query, params))
        return Row(
            {"matching_rows": self.count}
        ) if len(self.calls) == 1 else Row({"cancelled": self.cancelled})


class Session:
    def __init__(self, tx):
        self.tx = tx

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute_write(self, fn, *args):
        return fn(self.tx, *args)


class Neo:
    def __init__(self, tx):
        self.tx = tx

    def _session(self):
        return Session(self.tx)


def test_intent_cancel_acquires_canonical_node_lock_before_read():
    tx = Tx()
    n = cancellation.cancel_legacy_intent_tasks(
        Neo(tx), intent_id="synthetic-intent", reason="unit-only",
    )
    assert n == 2
    assert len(tx.calls) == 2
    assert "MATCH (w:WorkSupplyState {name:'canonical'})" in tx.calls[0][0]
    assert "SET w.legacy_intent_cancel_serial" in tx.calls[0][0]
    assert "MATCH (i:Intent" in tx.calls[1][0]
    assert "t.machine_authorized,false" in tx.calls[1][0]
    assert "t.machine_token_epoch IS NULL" in tx.calls[1][0]
    assert "t.machine_authorization_generation IS NULL" in tx.calls[1][0]
    assert "t.machine_authorization_approval_id IS NULL" in tx.calls[1][0]
    assert "t.hermes_claim_scope,false" in tx.calls[1][0]
    assert tx.calls[1][1] == {"intent_id": "synthetic-intent", "reason": "unit-only"}


def test_missing_canonical_lock_fails_before_task_mutation():
    tx = Tx(count=0)
    with pytest.raises(cancellation.LegacyIntentCancelBlocked, match="lock_not_unique"):
        cancellation.cancel_legacy_intent_tasks(
            Neo(tx), intent_id="synthetic-intent", reason="synthetic",
        )
    assert len(tx.calls) == 1


@pytest.mark.parametrize("bad_id", ["", " ", None, 17])
def test_invalid_intent_never_enters_db(bad_id):
    tx = Tx()
    with pytest.raises(cancellation.LegacyIntentCancelBlocked):
        cancellation.cancel_legacy_intent_tasks(
            Neo(tx), intent_id=bad_id, reason="test",
        )
    assert not tx.calls


def test_reason_bounded_and_wrong_type_denied():
    tx = Tx()
    assert cancellation.cancel_legacy_intent_tasks(
        Neo(tx), intent_id="synthetic", reason="a"*900,
    ) == 2
    assert len(tx.calls[1][1]["reason"]) == 500
    other = Tx()
    with pytest.raises(cancellation.LegacyIntentCancelBlocked):
        cancellation.cancel_legacy_intent_tasks(
            Neo(other), intent_id="synthetic", reason=None,
        )
    assert not other.calls


def test_both_legacy_intent_entrypoints_use_shared_fence():
    root = Path(__file__).resolve().parents[1] / "src" / "assistx"
    for file, method in (
        ("api.py", "_cancel_tasks_for_intent"),
        ("intent_orchestrator.py", "_handle_cancel"),
    ):
        tree = ast.parse((root/file).read_text())
        nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == method]
        assert len(nodes) == 1
        node = nodes[0]
        calls = [
            n.func.id
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        ]
        assert "cancel_legacy_intent_tasks" in calls
        assert not any(isinstance(n, ast.Attribute) and n.attr == "run" for n in ast.walk(node))


@pytest.mark.parametrize("count", [0, 2, 3])
def test_canonical_source_lock_must_be_exactly_one_row(count):
    tx = Tx(count=count)
    with pytest.raises(cancellation.LegacyIntentCancelBlocked, match="not_unique"):
        cancellation.cancel_legacy_intent_tasks(
            Neo(tx), intent_id="synthetic-intent", reason="test",
        )
    assert len(tx.calls) == 1
