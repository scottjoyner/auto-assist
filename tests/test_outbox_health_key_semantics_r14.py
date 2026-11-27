"""R14: pin the exact key set and key semantics of read_queue_health().

Every fixture uses a named shared-cache in-memory SQLite database; the probe's
filesystem/connect calls are patched on the module under test only. No real
file is created and no live outbox DB is opened or written.
"""

import os
import sqlite3
import uuid
from datetime import UTC, datetime

import pytest

import assistx.outbox_client as oc
from assistx.outbox_client import read_queue_health

MEM_PATH = ":memory:"
# Healthy path pops "reason": 15 base keys minus one = 14. This is the full
# key set on both the current and legacy schemas (both return status="ok").
EXPECTED_KEYS = {
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


def _stamp():
    return datetime.now(UTC).isoformat()


@pytest.fixture
def mem_health(monkeypatch):
    """One shared in-memory outbox behind every probe connection."""
    name = f"assistx-health-keys-r14-{uuid.uuid4().hex}"
    uri = f"file:{name}?mode=memory&cache=shared"
    sentinel = f"/fake/{name}.sqlite"  # never exists on disk
    real_connect = sqlite3.connect
    keeper = real_connect(uri, uri=True)
    state = {"legacy": False}

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

    class Handle:
        def make_schema(self, *, legacy=False):
            state["legacy"] = legacy
            extra = "" if legacy else ", claim_owner TEXT, claimed_at TEXT"
            keeper.execute(
                "CREATE TABLE outbox (outbox_id TEXT PRIMARY KEY, "
                "event_id TEXT NOT NULL, payload_json TEXT NOT NULL, "
                "attempt_count INTEGER DEFAULT 0, last_attempt_at TEXT, "
                "status TEXT DEFAULT 'pending', created_at TEXT NOT NULL"
                f"{extra})"
            )
            keeper.commit()

        def insert(self, outbox_id, *, status="pending", claim_owner=None):
            if state["legacy"]:
                assert claim_owner is None
                keeper.execute(
                    "INSERT INTO outbox (outbox_id, event_id, payload_json, "
                    "attempt_count, last_attempt_at, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (outbox_id, f"evt-{outbox_id}", "{}", 0, None, status, _stamp()),
                )
            else:
                keeper.execute(
                    "INSERT INTO outbox (outbox_id, event_id, payload_json, "
                    "attempt_count, last_attempt_at, status, created_at, "
                    "claim_owner, claimed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        outbox_id,
                        f"evt-{outbox_id}",
                        "{}",
                        0,
                        None,
                        status,
                        _stamp(),
                        claim_owner,
                        None,
                    ),
                )
            keeper.commit()

        def health(self, **kwargs):
            return read_queue_health(sentinel, **kwargs)

    assert not os.path.lexists(MEM_PATH)
    assert not os.path.exists(sentinel)
    try:
        yield Handle()
    finally:
        keeper.close()
        assert not os.path.lexists(MEM_PATH)  # no on-disk DB was ever created
        assert not os.path.exists(sentinel)


def test_full_keyset_and_claimable_plus_quarantined_equals_pending(mem_health):
    """Mixed fixture pins the full key set and the documented identity."""
    mem_health.make_schema()
    # 3 plain pending + 4 operator-held pending + 1 'quarantined:' near-miss.
    for i in range(3):
        mem_health.insert(f"pending-{i}")
    for i in range(4):
        mem_health.insert(f"held-{i}", status="pending", claim_owner=f"quarantine:hold-{i}:r14")
    # Near-miss: no colon directly after 'quarantine', so still claimable.
    mem_health.insert("decoy", claim_owner="quarantined:not-a-hold")
    # Other statuses contribute to total but not to the pending buckets.
    mem_health.insert("claimed", status="claimed", claim_owner=uuid.uuid4().hex)
    mem_health.insert("failed", status="failed")
    mem_health.insert("delivered", status="delivered")
    mem_health.insert("dead", status="dead")

    health = mem_health.health()

    # Exact response shape: no extra keys, and reason is absent on the ok path.
    assert set(health) == EXPECTED_KEYS
    assert "reason" not in health
    # No retry/attempt buckets exist in this revision's contract.
    assert "retry" not in health
    assert "attempt_buckets" not in health

    # pending = 3 plain + 4 held + 1 near-miss; held rows keep status=pending.
    assert health["pending"] == 8
    assert health["quarantined"] == 4
    assert health["claimable_pending"] == 4
    assert health["claimable_pending"] + health["quarantined"] == health["pending"]
    assert health["claimed"] == 1
    assert health["failed"] == 1
    assert health["delivered"] == 1
    assert health["dead"] == 1
    assert health["total"] == 12
    assert health["status"] == "ok"
    assert health["ok"] is True
    assert isinstance(health["db_path"], str)
    assert isinstance(health["stale_after_s"], int)
    assert isinstance(health["oldest_pending_at"], str)
    assert isinstance(health["oldest_pending_age_s"], float)
    assert health["oldest_pending_age_s"] >= 0.0


def test_legacy_schema_fallback_preserves_full_keyset(mem_health):
    """Without claim_owner, quarantined=0 and claimable_pending=pending."""
    mem_health.make_schema(legacy=True)
    for i in range(2):
        mem_health.insert(f"legacy-{i}")

    health = mem_health.health()

    assert set(health) == EXPECTED_KEYS  # full set, same as current schema
    assert health["claimable_pending"] == health["pending"] == 2
    assert health["quarantined"] == 0
    assert health["total"] == 2
    assert health["status"] == "ok"
    assert health["ok"] is True
    assert "reason" not in health  # missing claim_owner is not an error path
