"""Outbox health reports claimable_pending without touching a live database.

All fixtures below use a named shared-cache in-memory SQLite database. The
health probe's filesystem/connect calls are patched on the module under test
only; no real file is created and no live outbox is opened. The error-path
test makes the patched connect raise, exercising the probe's error branch.
"""

import os
import sqlite3
import uuid
from datetime import UTC, datetime

import pytest

import assistx.outbox_client as oc
from assistx.outbox_client import read_queue_health

MEM_PATH = ":memory:"
# Healthy response pops "reason"; error response keeps it (and status="error").
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
ERROR_KEYS = EXPECTED_KEYS | {"reason"}


def _stamp():
    return datetime.now(UTC).isoformat()


@pytest.fixture
def mem_health(monkeypatch):
    """One shared in-memory outbox behind every probe connection."""
    name = f"assistx-claimable-health-r13-{uuid.uuid4().hex}"
    uri = f"file:{name}?mode=memory&cache=shared"
    sentinel = f"/fake/{name}.sqlite"  # never exists on disk
    real_connect = sqlite3.connect
    keeper = real_connect(uri, uri=True)
    state = {"legacy": False, "broken": False}

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
            if state["broken"]:
                raise sqlite3.OperationalError("simulated unreadable outbox")
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
            row = keeper.execute(
                "SELECT status, claim_owner FROM outbox WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()
            assert row == (status, claim_owner)

        def break_connect(self):
            state["broken"] = True

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


def test_zero_quarantined_claimable_equals_pending(mem_health):
    mem_health.make_schema()
    for i in range(3):
        mem_health.insert(f"pending-{i}")
    # A normal claim token must not reduce the claimable pending count.
    mem_health.insert("claimed", status="claimed", claim_owner=uuid.uuid4().hex)

    health = mem_health.health()

    assert health["claimable_pending"] == health["pending"] == 3
    assert health["quarantined"] == 0
    assert health["claimed"] == 1
    assert health["failed"] == 0
    assert health["dead"] == 0
    assert health["total"] == 4
    assert health["status"] == "ok"
    assert health["ok"] is True
    # Additive change: one new key only, all pre-existing keys retained.
    assert set(health) == EXPECTED_KEYS


def test_fourteen_quarantined_claimable_is_pending_minus_14(mem_health):
    mem_health.make_schema()
    for i in range(14):
        mem_health.insert(
            f"held-{i}",
            status="pending",
            claim_owner=f"quarantine:hold-{i}:r4-20261126",
        )
    for i in range(5):
        mem_health.insert(f"pending-{i}")
    # Near-miss: 'quarantined:' has no colon right after 'quarantine', so this
    # row is still claimable (it does not carry a quarantine marker).
    mem_health.insert("decoy", claim_owner="quarantined:not-a-hold")
    mem_health.insert("normal-claim", status="claimed", claim_owner=uuid.uuid4().hex)

    health = mem_health.health()

    # All 20 pending rows (14 held + 5 plain + 1 decoy) stay in `pending`; the
    # 14 held rows are excluded from claimable_pending.
    assert health["pending"] == 20
    assert health["quarantined"] == 14
    assert health["claimable_pending"] == health["pending"] - 14 == 6
    assert health["total"] == 21
    assert health["status"] == "ok"
    assert health["ok"] is True
    # Read-only: the probe never rewrites a quarantine marker it excludes.
    mem_health.assert_unchanged("held-0", status="pending", claim_owner="quarantine:hold-0:r4-20261126")
    # Same count on the non-read-only connection path used by queue_health().
    assert mem_health.health(read_only=False)["claimable_pending"] == 6


def test_legacy_schema_claimable_equals_pending(mem_health):
    mem_health.make_schema(legacy=True)
    for i in range(2):
        mem_health.insert(f"legacy-{i}")

    health = mem_health.health()

    assert health["claimable_pending"] == health["pending"] == 2
    assert health["quarantined"] == 0
    assert health["total"] == 2
    assert health["status"] == "ok"
    assert health["ok"] is True
    assert "reason" not in health  # missing claim_owner column is not an error
    assert set(health) == EXPECTED_KEYS


def test_error_path_keeps_claimable_key_without_raising(mem_health):
    mem_health.make_schema()
    mem_health.insert("pending-0")
    mem_health.break_connect()

    # The probe's outer except sqlite3.Error must absorb the failure.
    health = mem_health.health()

    assert health["status"] == "error"
    assert health["ok"] is True
    assert health["reason"].startswith("outbox unreadable:")
    # Additive key is present (0) on the error path, and no other key is lost.
    assert health["claimable_pending"] == 0
    assert set(health) == ERROR_KEYS
