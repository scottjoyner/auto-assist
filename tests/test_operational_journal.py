from __future__ import annotations

import json

import pytest

from assistx.operational_journal import (
    AppendOnlyOperationJournal,
    JournalCorruption,
)


def test_journal_is_hash_chained_and_exact_retries_are_idempotent(tmp_path):
    journal = AppendOnlyOperationJournal(
        tmp_path / "operations.jsonl",
        clock_ms=lambda: 1_000,
    )
    first = journal.append(
        idempotency_key="task:1",
        status="PENDING",
        payload={"task_id": "1"},
    )
    duplicate = journal.append(
        idempotency_key="task:1",
        status="PENDING",
        payload={"task_id": "1"},
    )

    assert first == duplicate
    assert journal.verify()["entries"] == 1
    assert journal.pending()[0].idempotency_key == "task:1"


def test_replay_commits_once_with_deterministic_commit_identity(tmp_path):
    journal = AppendOnlyOperationJournal(tmp_path / "operations.jsonl")
    journal.append(
        idempotency_key="finalize:task-1",
        status="PENDING",
        payload={
            "operation_id": "task-1",
            "operation_kind": "task_outcome",
            "final_state": "COMPLETED",
        },
    )
    commits = []

    first = journal.replay(lambda envelope: commits.append(envelope) or "ok")
    second = journal.replay(lambda envelope: commits.append(envelope) or "duplicate")

    assert first["committed"] == 1
    assert second["attempted"] == 0
    assert len(commits) == 1
    assert commits[0]["durable_commit_id"].startswith("durable-")
    assert journal.pending() == []


def test_failed_replay_remains_pending_until_neo4j_returns(tmp_path):
    journal = AppendOnlyOperationJournal(tmp_path / "operations.jsonl")
    journal.append(
        idempotency_key="finalize:task-2",
        status="PENDING",
        payload={"operation_id": "task-2"},
    )

    failed = journal.replay(
        lambda _envelope: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    assert failed["failed"] == 1
    assert failed["remaining"] == 1

    recovered = journal.replay(lambda _envelope: {"status": "durable"})
    assert recovered["committed"] == 1
    assert recovered["remaining"] == 0


def test_journal_tampering_is_detected_before_replay(tmp_path):
    path = tmp_path / "operations.jsonl"
    journal = AppendOnlyOperationJournal(path)
    journal.append(
        idempotency_key="finalize:task-3",
        status="PENDING",
        payload={"operation_id": "task-3"},
    )
    value = json.loads(path.read_text().strip())
    value["payload"]["operation_id"] = "tampered"
    path.write_text(json.dumps(value) + "\n")

    with pytest.raises(JournalCorruption, match="checksum mismatch"):
        journal.verify()
