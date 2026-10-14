"""Offline-only coverage reconciliation tests; no graph, network or replay."""
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from assistx import task_notification_audit as subject

NOW = datetime(2026, 10, 14, 0, 0, tzinfo=UTC)
TASK_IDS = ["synthetic-task-a", "synthetic-task-b"]


def _snapshots(task_ids=None, receiver_keys=None, *, captured_at=None):
    stamp = (captured_at or NOW).isoformat()
    tasks = {
        "schema": subject.TASK_SCHEMA,
        "complete": True,
        "captured_at": stamp,
        "task_ids": list(TASK_IDS if task_ids is None else task_ids),
    }
    inbound = {
        "schema": subject.RECEIVER_SCHEMA,
        "complete": True,
        "captured_at": stamp,
        "idempotency_keys": list(receiver_keys or []),
    }
    return tasks, inbound


def _outbox(tmp_path, entries=()):
    path = tmp_path / "synthetic-outbox.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE outbox(event_id TEXT, status TEXT, payload_json TEXT)"
        )
        for index, (task_id, status) in enumerate(entries):
            conn.execute(
                "INSERT INTO outbox(event_id,status,payload_json) VALUES(?,?,?)",
                (
                    f"evt_synthetic{index}",
                    status,
                    json.dumps(
                        {
                            "event_type": "task.candidate.created",
                            "idempotency_key": f"assistx-task-created:{task_id}",
                            "subject": {"task_id": task_id},
                        }
                    ),
                ),
            )
    return str(path)


def test_both_receiver_witnesses_complete_only_source_declared(tmp_path):
    tasks, inbound = _snapshots(
        receiver_keys=["assistx-task-created:synthetic-task-a",
                       "assistx-task-created:synthetic-task-b"]
    )
    result = subject.audit(tasks, inbound, _outbox(tmp_path), now=NOW)
    assert result["status"] == "PASS_SOURCE_DECLARED_INGEST_COVERAGE_ONLY"
    assert result["category_counts"] == {"RECEIVER_INGEST_WITNESS": 2}
    assert result["production_release_authorized"] is False
    assert result["receiver_ingest_is_not_task_processing"] is True


def test_missing_notifier_witness_is_hold(tmp_path):
    tasks, inbound = _snapshots()
    result = subject.audit(tasks, inbound, _outbox(tmp_path), now=NOW)
    assert result["status"] == "HOLD_INCOMPLETE_NOTIFICATION_WITNESS"
    assert result["category_counts"] == {"HOLD_NO_NOTIFICATION_WITNESS": 2}


def test_queued_is_not_equivalent_to_receiver_ingest(tmp_path):
    tasks, inbound = _snapshots()
    result = subject.audit(
        tasks, inbound, _outbox(tmp_path, [("synthetic-task-a", "pending")]),
        now=NOW,
    )
    assert result["category_counts"] == {
        "HOLD_NO_NOTIFICATION_WITNESS": 1,
        "LOCAL_PENDING_ONLY": 1,
    }


@pytest.mark.parametrize(
    ("local_status", "expected"),
    [
        ("dead", "HOLD_DEAD_LOCAL"),
        ("delivered", "HOLD_DELIVERED_LOCAL_NOT_RECEIVER_ATTESTED"),
        ("failed", "LOCAL_PENDING_ONLY"),
    ],
)
def test_local_delivery_status_never_substitutes_receiver_evidence(
    tmp_path, local_status, expected
):
    tasks, inbound = _snapshots(task_ids=["synthetic-task-a"])
    result = subject.audit(
        tasks, inbound, _outbox(tmp_path, [("synthetic-task-a", local_status)]),
        now=NOW,
    )
    assert result["category_counts"] == {expected: 1}
    assert result["status"] == "HOLD_INCOMPLETE_NOTIFICATION_WITNESS"


def test_duplicate_local_ids_are_review_only_even_with_receiver_witness(tmp_path):
    tasks, inbound = _snapshots(
        task_ids=["synthetic-task-a"],
        receiver_keys=["assistx-task-created:synthetic-task-a"],
    )
    result = subject.audit(
        tasks,
        inbound,
        _outbox(
            tmp_path,
            [("synthetic-task-a", "pending"), ("synthetic-task-a", "delivered")],
        ),
        now=NOW,
    )
    assert result["category_counts"] == {"HOLD_LOCAL_DUPLICATE": 1}


@pytest.mark.parametrize(
    "mutation",
    [
        {"complete": False},
        {"schema": "unknown"},
        {"captured_at": (NOW - timedelta(hours=1)).isoformat()},
        {"captured_at": (NOW + timedelta(minutes=3)).isoformat()},
        {"task_ids": ["synthetic-task-a", "synthetic-task-a"]},
        {"task_ids": []},
    ],
)
def test_missing_stale_duplicate_or_untrusted_task_export_denied(
    tmp_path, mutation
):
    tasks, inbound = _snapshots()
    tasks.update(mutation)
    with pytest.raises(ValueError):
        subject.audit(tasks, inbound, _outbox(tmp_path), now=NOW)


def test_noncoincident_source_snapshots_denied(tmp_path):
    tasks, inbound = _snapshots()
    inbound["captured_at"] = (NOW - timedelta(minutes=6)).isoformat()
    with pytest.raises(ValueError, match="too far apart"):
        subject.audit(tasks, inbound, _outbox(tmp_path), now=NOW)


def test_corrupted_outbox_payload_rejects_whole_report(tmp_path):
    tasks, inbound = _snapshots()
    path = _outbox(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO outbox VALUES(?,?,?)",
            ("evt_bad", "pending", "{not JSON"),
        )
    with pytest.raises(ValueError, match="valid JSON"):
        subject.audit(tasks, inbound, path, now=NOW)


def test_inconsistent_task_key_is_never_silently_ignored(tmp_path):
    tasks, inbound = _snapshots()
    path = _outbox(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO outbox VALUES(?,?,?)",
            (
                "evt_bad",
                "pending",
                json.dumps(
                    {
                        "event_type": "task.candidate.created",
                        "idempotency_key": "assistx-task-created:synthetic-task-b",
                        "subject": {"task_id": "synthetic-task-a"},
                    }
                ),
            ),
        )
    with pytest.raises(ValueError, match="provenance mismatch"):
        subject.audit(tasks, inbound, path, now=NOW)


def test_offline_sqlite_is_not_mutated(tmp_path):
    tasks, inbound = _snapshots()
    path = _outbox(tmp_path, [("synthetic-task-a", "pending")])
    before = (tmp_path / "synthetic-outbox.sqlite").read_bytes()
    subject.audit(tasks, inbound, path, now=NOW)
    assert (tmp_path / "synthetic-outbox.sqlite").read_bytes() == before
