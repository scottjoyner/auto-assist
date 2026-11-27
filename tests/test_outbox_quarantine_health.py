"""Outbox health reports quarantine markers without touching a live database.

All fixtures below use a named shared-cache in-memory SQLite database. The
health probe's filesystem/connect calls are patched on the module under test
only; no real file is created and no live outbox is opened.
"""

import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import assistx.outbox_client as oc
from assistx.outbox_client import read_queue_health

MEM_PATH = ":memory:"
# The healthy response pops "reason"; it is present only when absent/stale/error.
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
    "total",
    "oldest_pending_at",
    "oldest_pending_age_s",
}


def _stamp():
    return datetime.now(UTC).isoformat()


@pytest.fixture
def mem_health(monkeypatch):
    """One shared in-memory outbox behind every probe connection."""
    name = f"assistx-quarantine-health-r12-{uuid.uuid4().hex}"
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

        def assert_unchanged(self, outbox_id, *, status, claim_owner):
            row = keeper.execute("SELECT status, claim_owner FROM outbox WHERE outbox_id=?", (outbox_id,)).fetchone()
            assert row == (status, claim_owner)

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


def test_zero_quarantined_rows(mem_health):
    mem_health.make_schema()
    for i in range(3):
        mem_health.insert(f"pending-{i}")
    # A normal claim token and a near-miss label must not be counted.
    mem_health.insert("claimed", status="claimed", claim_owner=uuid.uuid4().hex)

    health = mem_health.health()

    assert health["quarantined"] == 0
    assert health["pending"] == 3
    assert health["claimed"] == 1
    assert health["failed"] == 0
    assert health["dead"] == 0
    assert health["total"] == 4
    assert health["status"] == "ok"
    assert health["ok"] is True
    # Additive change: every old key is still present, with one new key only.
    assert set(health) == EXPECTED_KEYS


def test_fourteen_quarantine_markers_mixed_with_pending(mem_health):
    mem_health.make_schema()
    for i in range(14):
        mem_health.insert(f"held-{i}", status="pending", claim_owner=f"quarantine:hold-{i}:r4-20261126")
    for i in range(5):
        mem_health.insert(f"pending-{i}")
    # Near-miss: 'quarantined:' lacks the colon right after 'quarantine'.
    mem_health.insert("decoy", claim_owner="quarantined:not-a-hold")
    mem_health.insert("normal-claim", status="claimed", claim_owner=uuid.uuid4().hex)

    health = mem_health.health()

    # Quarantine is a claim_owner label, not a status: the 14 held rows still
    # count as pending, and the count is reported separately.
    assert health["quarantined"] == 14
    assert health["pending"] == 20
    assert health["claimed"] == 1
    assert health["total"] == 21
    assert health["status"] == "ok"
    assert health["ok"] is True
    # The probe is read-only and never rewrites the markers it counts.
    mem_health.assert_unchanged("held-0", status="pending", claim_owner="quarantine:hold-0:r4-20261126")
    # Same count on the non-read-only connection path used by queue_health().
    assert mem_health.health(read_only=False)["quarantined"] == 14


def test_legacy_schema_without_claim_owner_reports_zero(mem_health):
    mem_health.make_schema(legacy=True)
    for i in range(2):
        mem_health.insert(f"legacy-{i}")

    health = mem_health.health()

    assert health["quarantined"] == 0
    assert health["pending"] == 2
    assert health["total"] == 2
    assert health["status"] == "ok"
    assert health["ok"] is True
    assert "reason" not in health  # the missing column is not an error
    assert set(health) == EXPECTED_KEYS
