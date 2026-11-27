"""R27: derive the documented outbox numbers from one in-memory fixture.

The cross-store doc quotes the AssistX outbox side of a read-only picture:
48 total / 48 pending, 14 quarantine markers, 34 claimable pending and an
attempt distribution of 0:28 / 3:6 / 9:14. This single test rebuilds that exact
distribution in a shared-cache in-memory SQLite database and checks that every
number is derivable from it, that quarantine rows are excluded from
``claimable_pending`` and that the two partition identities hold.

Only the module under test is patched, so no file is created and no live outbox
DB is opened or written. No production code is changed. This is a contract
test, not a capacity measurement, and it makes no claim about the separate
SensorGraph coordination store.
"""

import sqlite3
import uuid
from datetime import UTC, datetime

import assistx.outbox_client as oc
from assistx.outbox_client import read_queue_health

# Doc-quoted fixture distribution: 28 rows at attempt 0, 6 at attempt 3 and 14
# at attempt 9. The 14 attempt-9 rows carry the R4-style quarantine marker;
# those are exactly the rows excluded from claimable_pending.
ATTEMPT_DISTRIBUTION = {0: 28, 3: 6, 9: 14}
QUARANTINE_MARKER = "quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126"


def test_documented_outbox_numbers_are_derivable_and_quarantine_excluded(monkeypatch):
    name = f"assistx-cross-store-r27-{uuid.uuid4().hex}"
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
            # single in-memory database kept alive for this test.
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

    stamp = datetime.now(UTC).isoformat()
    row_id = 0
    for attempt_count, count in ATTEMPT_DISTRIBUTION.items():
        for _ in range(count):
            owner = QUARANTINE_MARKER if attempt_count == 9 else None
            keeper.execute(
                "INSERT INTO outbox (outbox_id, event_id, payload_json, "
                "attempt_count, last_attempt_at, status, created_at, "
                "claim_owner, claimed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"row-{row_id}",
                    f"evt-{row_id}",
                    "{}",
                    attempt_count,
                    None,
                    "pending",
                    stamp,
                    owner,
                    None,
                ),
            )
            row_id += 1
    keeper.commit()

    try:
        health = read_queue_health(sentinel, now=datetime.now(UTC))

        # Health-probe numbers quoted in docs/cross-store-utilization-r27.md.
        assert health["status"] == "ok"
        assert health["ok"] is True
        assert health["total"] == 48
        assert health["pending"] == 48
        assert health["quarantined"] == 14
        assert health["claimable_pending"] == 34
        # Every marker sits on a pending row, so the identity is exact here.
        assert health["claimable_pending"] + health["quarantined"] == health["pending"] == 48
        # All 48 rows are pending: no other status bucket contributes.
        assert all(health[bucket] == 0 for bucket in ("failed", "claimed", "delivered", "dead"))

        # The quarantine rows are exactly the 14 attempt-9 rows and none of
        # them is counted as claimable; all 34 claimable rows are unmarked.
        conn = real_connect(uri, uri=True)
        try:
            marked = conn.execute(
                "SELECT attempt_count, COUNT(*) FROM outbox "
                "WHERE claim_owner LIKE 'quarantine:%' GROUP BY attempt_count"
            ).fetchall()
            assert marked == [(9, 14)]

            claimable = conn.execute(
                f"SELECT COUNT(*) FROM outbox WHERE status='pending' AND {oc._QUARANTINE_CLAIM_GUARD}"
            ).fetchone()[0]
            assert claimable == 34 == health["claimable_pending"]
            assert claimable == 48 - 14

            # Audit-CLI-style attempt_distribution (GROUP BY attempt_count);
            # keys are strings via the CLI's label rendering.
            attempts = {
                str(value): count
                for value, count in conn.execute(
                    'SELECT "attempt_count", COUNT(*) FROM outbox GROUP BY "attempt_count" ORDER BY "attempt_count"'
                ).fetchall()
            }
            assert attempts == {"0": 28, "3": 6, "9": 14}
            assert sum(attempts.values()) == health["total"] == 48
        finally:
            conn.close()
    finally:
        keeper.close()
