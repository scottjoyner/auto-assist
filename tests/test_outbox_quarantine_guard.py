"""Quarantine markers in claim_owner are holds, never claimable work."""

import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import assistx.outbox_client as oc
from assistx.outbox_client import OutboxClient

MARKER = "quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126"
MEM_PATH = ":memory:"


def _stamp(seconds_ago=0):
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


class _OneRow:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _SelectIntercept:
    """Force one claim SELECT to return a chosen row; delegate everything else."""

    def __init__(self, conn, forced_row):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_forced_row", forced_row)

    def execute(self, sql, params=()):
        if sql.lstrip().startswith("SELECT outbox_id, event_id, payload_json"):
            return _OneRow(self._forced_row)
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)


@pytest.fixture
def mem_outbox(monkeypatch):
    """One shared in-memory SQLite database behind every OutboxClient connection.

    OutboxClient opens a fresh connection per operation, so a bare ":memory:"
    would give each operation a different empty database. Route those connects
    to one named shared-cache database kept alive by ``keeper``. No file is
    created, and no live database is opened.
    """
    name = f"assistx-quarantine-r11-{uuid.uuid4().hex}"
    uri = f"file:{name}?mode=memory&cache=shared"
    real_connect = sqlite3.connect
    keeper = real_connect(uri, uri=True)
    state = {"forced": None}

    class SqliteShim:
        Error = sqlite3.Error
        Connection = sqlite3.Connection

        @staticmethod
        def connect(database, *args, **kwargs):
            if database != MEM_PATH:
                return real_connect(database, *args, **kwargs)
            kwargs["uri"] = True
            conn = real_connect(uri, *args, **kwargs)
            forced = state["forced"]
            if forced is not None:
                state["forced"] = None
                return _SelectIntercept(conn, forced)
            return conn

    # Patch only the module under test, not the global sqlite3 module.
    monkeypatch.setattr(oc, "sqlite3", SqliteShim)

    class MemOutbox:
        def client(self, **kwargs):
            return OutboxClient(
                db_path=MEM_PATH,
                api_url="http://assistx.invalid",
                auto_flush=False,
                **kwargs,
            )

        def insert(
            self,
            outbox_id,
            *,
            created_at=None,
            status="pending",
            attempt_count=0,
            claim_owner=None,
            claimed_at=None,
            last_attempt_at=None,
        ):
            keeper.execute(
                "INSERT INTO outbox (outbox_id, event_id, payload_json, attempt_count, "
                "last_attempt_at, status, created_at, claim_owner, claimed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    outbox_id,
                    f"evt-{outbox_id}",
                    '{"k": "v"}',
                    attempt_count,
                    last_attempt_at,
                    status,
                    created_at or _stamp(60),
                    claim_owner,
                    claimed_at,
                ),
            )
            keeper.commit()

        def row(self, outbox_id):
            return keeper.execute(
                "SELECT status, attempt_count, claim_owner, claimed_at, last_attempt_at FROM outbox WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()

        def full_row(self, outbox_id):
            return keeper.execute(
                "SELECT outbox_id, event_id, payload_json, attempt_count, "
                "last_attempt_at, status, created_at FROM outbox WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()

        def age_claim(self, outbox_id, seconds=3600):
            keeper.execute(
                "UPDATE outbox SET status='claimed', claim_owner='ghost', claimed_at=? WHERE outbox_id=?",
                (_stamp(seconds), outbox_id),
            )
            keeper.commit()

        def force_next_claim_select(self, row):
            state["forced"] = row

    assert not os.path.lexists(MEM_PATH)
    try:
        yield MemOutbox()
    finally:
        keeper.close()
        assert not os.path.lexists(MEM_PATH)  # no on-disk DB was ever created


def test_quarantined_pending_row_is_not_claimed(mem_outbox):
    client = mem_outbox.client()
    mem_outbox.insert(
        "quarantined",
        created_at=_stamp(120),
        status="pending",
        attempt_count=9,
        claim_owner=MARKER,
    )
    mem_outbox.insert("normal", created_at=_stamp(60), status="pending")

    claimed = client._claim_next()
    assert claimed is not None
    assert claimed.outbox_id == "normal"
    assert claimed.claim_owner and not claimed.claim_owner.startswith("quarantine:")

    # Even alone as the only remaining candidate, the marked row stays put.
    assert client._claim_next(exclude=["normal"]) is None
    assert client._claim_next() is None
    assert mem_outbox.row("quarantined") == ("pending", 9, MARKER, None, None)


def test_normal_pending_row_is_claimed(mem_outbox):
    client = mem_outbox.client()
    mem_outbox.insert("old", created_at=_stamp(120))
    mem_outbox.insert("new", created_at=_stamp(60))

    claimed = client._claim_next()
    assert claimed is not None
    assert claimed.outbox_id == "old"
    assert claimed.status == "claimed"
    assert claimed.claim_owner
    assert mem_outbox.row("old")[0] == "claimed"
    assert mem_outbox.row("new")[0] == "pending"


def test_quarantine_marker_on_attempt9_row_stays_untouched(mem_outbox, monkeypatch):
    client = mem_outbox.client()
    last_attempt = _stamp(3600)
    mem_outbox.insert(
        "wedged",
        created_at=_stamp(7200),
        status="pending",
        attempt_count=9,
        claim_owner=MARKER,
        last_attempt_at=last_attempt,
    )
    mem_outbox.insert("normal", created_at=_stamp(60))

    delivered = []
    monkeypatch.setattr(
        OutboxClient,
        "_deliver",
        lambda self, entry: delivered.append(entry.outbox_id) or True,
    )
    # Default max_retries is 10, so attempt 9 would otherwise be delivered.
    assert client.flush() == 1
    # A lower ceiling would otherwise move it to terminal 'dead'.
    assert client.flush(max_attempts=5) == 0
    assert delivered == ["normal"]
    assert mem_outbox.row("wedged") == ("pending", 9, MARKER, None, last_attempt)
    assert mem_outbox.row("normal")[0] == "delivered"


def test_expired_quarantine_claim_is_not_recovered(mem_outbox, monkeypatch):
    client = mem_outbox.client(claim_lease_s=1)
    stale_claim_at = _stamp(3600)
    mem_outbox.insert(
        "quarantine-expired",
        created_at=_stamp(7200),
        status="claimed",
        attempt_count=9,
        claim_owner=MARKER,
        claimed_at=stale_claim_at,
    )
    mem_outbox.insert("normal", created_at=_stamp(60))
    monkeypatch.setattr(OutboxClient, "_deliver", lambda self, entry: True)

    assert client.flush() == 1
    assert mem_outbox.row("quarantine-expired") == (
        "claimed",
        9,
        MARKER,
        stale_claim_at,
        None,
    )


def test_existing_claim_behavior_is_unchanged(mem_outbox):
    owner = mem_outbox.client()
    mem_outbox.insert("normal", created_at=_stamp(60))

    first = owner._claim_next()
    assert first is not None and first.outbox_id == "normal"

    # A live claim is still exclusive to its owner.
    rival = mem_outbox.client()
    assert rival._claim_next() is None

    # An expired claim on a normal row is still recoverable.
    mem_outbox.age_claim("normal")
    recovered = rival._claim_next()
    assert recovered is not None
    assert recovered.outbox_id == "normal"
    assert recovered.claim_owner != "ghost"
    assert owner._release(first, "delivered", 1) is False
    assert rival._release(recovered, "pending", 0, touch=False) is True
    assert mem_outbox.row("normal")[0] == "pending"


def test_guarded_update_refuses_a_quarantined_row_even_if_selected(mem_outbox):
    """The UPDATE re-checks the marker, not just the SELECT (race defence)."""
    client = mem_outbox.client()
    mem_outbox.insert(
        "sneaked",
        created_at=_stamp(60),
        status="pending",
        attempt_count=9,
        claim_owner=MARKER,
    )
    mem_outbox.force_next_claim_select(mem_outbox.full_row("sneaked"))

    assert client._claim_next() is None
    assert mem_outbox.row("sneaked") == ("pending", 9, MARKER, None, None)
