"""R26: pin the coordination metrics the outbox can report read-only.

One in-memory fixture with quarantine markers, a session-id-shaped (non-hold)
owner and retry/terminal rows. The health probe's filesystem/connect calls are
patched on the module under test only: no real file is created and no live
outbox DB is opened or written. Also checks the audit CLI's fixed JSON key set
(its ``_result`` helper is pure and touches no DB). No production change.
"""

import importlib.util
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

import assistx.outbox_client as oc
from assistx.outbox_client import read_queue_health

# Every key read_queue_health() returns on the healthy path (reason popped).
HEALTH_KEYS = {
    "db_path",
    "status",
    "ok",
    "stale_after_s",
    "pending",
    "failed",
    "claimed",
    "delivered",
    "dead",
    "quarantined",
    "claimable_pending",
    "total",
    "oldest_pending_at",
    "oldest_pending_age_s",
}
STATUS_BUCKETS = ("pending", "failed", "claimed", "delivered", "dead")
AUDIT_KEYS = {
    "db",
    "size_bytes",
    "mtime",
    "detected_columns",
    "total_rows",
    "status_distribution",
    "attempt_distribution",
    "claim_owner_counts",
    "pass",
}


def _stamp():
    return datetime.now(UTC).isoformat()


@pytest.fixture
def mixed_mem_outbox(monkeypatch):
    """One shared in-memory outbox behind every probe connection."""
    name = f"assistx-coord-metrics-r26-{uuid.uuid4().hex}"
    uri = f"file:{name}?mode=memory&cache=shared"
    sentinel = f"/fake/{name}.sqlite"  # never exists on disk
    real_connect = sqlite3.connect
    keeper = real_connect(uri, uri=True)

    class _Path:
        @staticmethod
        def expanduser(path):
            return path

        @staticmethod
        def exists(path):
            return path == sentinel

    class _Os:
        path = _Path

    class _Sqlite:
        Error = sqlite3.Error
        OperationalError = sqlite3.OperationalError
        Connection = sqlite3.Connection

        @staticmethod
        def connect(database, *args, **kwargs):
            # read_queue_health builds file:<sentinel>?mode=ro; route it to the
            # single in-memory database kept alive by this fixture.
            return real_connect(uri, uri=True)

    # Patch only the module under test, never the global os/sqlite3 modules.
    monkeypatch.setattr(oc, "os", _Os)
    monkeypatch.setattr(oc, "sqlite3", _Sqlite)

    keeper.execute(
        "CREATE TABLE outbox (outbox_id TEXT PRIMARY KEY, "
        "event_id TEXT NOT NULL, payload_json TEXT NOT NULL, "
        "attempt_count INTEGER DEFAULT 0, last_attempt_at TEXT, "
        "status TEXT DEFAULT 'pending', created_at TEXT NOT NULL, "
        "claim_owner TEXT, claimed_at TEXT)"
    )

    def insert(outbox_id, *, status="pending", attempt_count=0, claim_owner=None):
        keeper.execute(
            "INSERT INTO outbox (outbox_id, event_id, payload_json, "
            "attempt_count, last_attempt_at, status, created_at, "
            "claim_owner, claimed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                outbox_id,
                f"evt-{outbox_id}",
                "{}",
                attempt_count,
                None,
                status,
                _stamp(),
                claim_owner,
                None,
            ),
        )
        keeper.commit()

    try:
        yield sentinel, insert
    finally:
        keeper.close()


def test_health_keys_present_and_internally_consistent(mixed_mem_outbox):
    """Mixed fixture pins the reported keys, identities and the audit key set."""
    sentinel, insert = mixed_mem_outbox

    # 3 plain pending + 4 operator-held pending + 1 pending labelled with a
    # session-id-shaped owner (claimable: not a quarantine hold).
    for i in range(3):
        insert(f"pending-{i}")
    for i in range(4):
        insert(f"held-{i}", claim_owner=f"quarantine:hold-{i}:r26")
    insert("sess-labelled", claim_owner=f"sess-{uuid.uuid4().hex}")
    # Retry buckets: 'failed' rows at attempt 3 and 9; one exhausted 'dead' row.
    insert("retry-3", status="failed", attempt_count=3)
    insert("retry-9", status="failed", attempt_count=9)
    insert("exhausted", status="dead", attempt_count=10)
    insert("in-flight", status="claimed", claim_owner=uuid.uuid4().hex)
    insert("done", status="delivered")

    health = read_queue_health(sentinel, now=datetime.now(UTC))

    # (a) Exact key set present; no invented retry/attempt keys.
    assert set(health) == HEALTH_KEYS
    assert "reason" not in health
    assert "retry" not in health
    assert "retry_buckets" not in health
    assert "attempt_buckets" not in health

    # (b) Counts and identities. All quarantine markers sit on pending rows, so
    #     claimable_pending + quarantined == pending holds by the R26 contract.
    assert health["pending"] == 8
    assert health["quarantined"] == 4
    assert health["claimable_pending"] == 4
    assert health["claimable_pending"] + health["quarantined"] == health["pending"]
    assert health["failed"] == 2
    assert health["dead"] == 1
    assert health["claimed"] == 1
    assert health["delivered"] == 1
    assert health["total"] == sum(health[bucket] for bucket in STATUS_BUCKETS) == 13
    assert health["status"] == "ok"
    assert health["ok"] is True

    # (c) The audit CLI's fixed JSON key set contains the attempt/retry detail.
    audit_path = Path(__file__).resolve().parents[1] / "tools" / "audit_outbox_ro.py"
    spec = importlib.util.spec_from_file_location("audit_outbox_ro_r26", audit_path)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    result = audit._result("fake.db")
    assert set(result) == AUDIT_KEYS
    assert isinstance(result["attempt_distribution"], dict)
    assert isinstance(result["status_distribution"], dict)
    # Non-hold owners are opaque; quarantine markers are the only label shown.
    assert audit._owner_label("quarantine:hold:r26") == "quarantine:hold:r26"
    assert audit._owner_label(f"sess-{uuid.uuid4().hex}") == "<redacted>"
