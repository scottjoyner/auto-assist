"""R29-4: claim-owner lifecycle for session-shaped owners at live scale.

Mirrors the *five registered MCP sessions* observed in Round 28 by count only:
this module never connects to MCP, reads no credentials or env snapshots, and
writes only to a temp SQLite file under pytest's ``tmp_path`` (TMPDIR is on the
SSD). The five session-shaped labels below are synthetic stand-ins, not real
session ids.

Covers the R23 optional ``owner=`` parameter end to end: one claim per session
owner, unique ``claim_owner`` values, quarantine markers excluded from both
claims and health counts, and the ``claim_owner`` token acting as the release
fence so a heartbeat/renewal presented by a different session cannot take over
a live claim.

No production code is changed. This is a contract test, not a capacity claim;
it makes no claim about the separate SensorGraph coordination store.
"""

import sqlite3
import uuid
from datetime import UTC, datetime

import pytest

import assistx.outbox_client as oc

# Synthetic session-shaped labels. Count (5) mirrors the five registered MCP
# sessions recorded in Round 28; the values are NOT real session ids and no MCP
# connection is made.
SESSION_OWNERS = [f"sess-r29-{i:04d}" for i in range(1, 6)]
OTHER_SESSION = "sess-r29-foreign"

QUARANTINE_MARKER = "quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126"
# Pin health evaluation so staleness never depends on the wall clock.
NOW = datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC)


def _connect(path):
    return sqlite3.connect(str(path))


@pytest.fixture
def seeded_client(tmp_path):
    """Temp-file outbox: 3 older quarantine rows + 5 claimable pending rows.

    The quarantine rows are deliberately the *oldest* rows: if the R11/R12/R13
    guard regressed they would be selected first, so their exclusion is proven
    by ordering rather than by count alone.
    """
    path = tmp_path / f"outbox-r29-{uuid.uuid4().hex}.sqlite"
    client = oc.OutboxClient(db_path=str(path), api_url="", auto_flush=False)
    conn = _connect(path)
    try:
        for i in range(3):
            conn.execute(
                "INSERT INTO outbox (outbox_id, event_id, payload_json, "
                "attempt_count, status, created_at, claim_owner) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"q-{i}",
                    f"evt-q-{i}",
                    "{}",
                    9,
                    "pending",
                    f"2026-11-26T23:59:{i:02d}+00:00",
                    QUARANTINE_MARKER,
                ),
            )
        for i in range(5):
            conn.execute(
                "INSERT INTO outbox (outbox_id, event_id, payload_json, "
                "attempt_count, status, created_at, claim_owner) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"n-{i}",
                    f"evt-n-{i}",
                    "{}",
                    0,
                    "pending",
                    f"2026-11-27T00:00:{i:02d}+00:00",
                    None,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return client


def _rows(client, where="1=1", params=()):
    conn = _connect(client.db_path)
    try:
        return conn.execute(
            "SELECT outbox_id, status, claim_owner FROM outbox WHERE " + where,
            params,
        ).fetchall()
    finally:
        conn.close()


def test_five_session_owners_each_claim_one_unique_row(seeded_client):
    """Each of the five session-shaped owners claims exactly one distinct row."""
    client = seeded_client
    entries = [client._claim_next(owner=owner) for owner in SESSION_OWNERS]

    assert all(entry is not None for entry in entries)
    owners = [entry.claim_owner for entry in entries]
    assert owners == SESSION_OWNERS
    assert len(set(owners)) == 5 == len(SESSION_OWNERS)
    assert len({entry.outbox_id for entry in entries}) == 5
    assert all(entry.status == "claimed" for entry in entries)

    # The five claimed rows are the normal rows; the older quarantine rows were
    # skipped despite sorting first, and keep their marker/status untouched.
    assert {entry.outbox_id for entry in entries} == {f"n-{i}" for i in range(5)}
    assert _rows(client, "outbox_id LIKE 'q-%'") == [
        (f"q-{i}", "pending", QUARANTINE_MARKER) for i in range(3)
    ]
    # Nothing else is deliverable: five live claims + three held rows.
    assert client._claim_next() is None


def test_claimable_pending_and_quarantined_stay_consistent(seeded_client):
    """Health counts remain exact across the 5-session claim burst."""
    client = seeded_client

    before = client.queue_health(now=NOW)
    assert before["status"] == "ok" and before["ok"] is True
    assert before["pending"] == 8
    assert before["quarantined"] == 3
    assert before["claimable_pending"] == 5
    assert before["claimable_pending"] + before["quarantined"] == before["pending"] == 8

    for owner in SESSION_OWNERS:
        assert client._claim_next(owner=owner) is not None

    after = client.queue_health(now=NOW)
    assert after["claimed"] == 5
    assert after["pending"] == 3  # only the held rows remain pending
    assert after["quarantined"] == 3
    assert after["claimable_pending"] == 0
    assert after["claimable_pending"] + after["quarantined"] == after["pending"] == 3
    assert after["total"] == before["total"] == 8


def test_cross_session_renewal_is_fenced_and_release_needs_matching_token(seeded_client):
    """A different session cannot renew/steal a live claim; release needs its token."""
    client = seeded_client
    entries = [client._claim_next(owner=owner) for owner in SESSION_OWNERS]
    first = entries[0]
    first_row = (first.outbox_id, "claimed", SESSION_OWNERS[0])
    assert _rows(client, "outbox_id=?", (first.outbox_id,)) == [first_row]

    # No deliverable row for a sixth/foreign session while all five leases live.
    assert client._claim_next(owner=OTHER_SESSION) is None
    assert _rows(client, "outbox_id=?", (first.outbox_id,)) == [first_row]

    # Forge a renewal/release with a different session's token: fenced out, the
    # original session's claim is left intact.
    forged = oc.OutboxEntry(
        outbox_id=first.outbox_id,
        event_id=first.event_id,
        payload_json=first.payload_json,
        attempt_count=first.attempt_count,
        status="claimed",
        created_at=first.created_at,
        claim_owner=SESSION_OWNERS[1],
        claimed_at=first.claimed_at,
    )
    assert client._release(forged, "delivered", 1) is False
    assert _rows(client, "outbox_id=?", (first.outbox_id,)) == [first_row]

    # Only the matching claim_owner token is accepted as the release fence.
    assert client._release(first, "delivered", first.attempt_count + 1) is True
    assert _rows(client, "outbox_id=?", (first.outbox_id,)) == [
        (first.outbox_id, "delivered", None)
    ]
    # The other four sessions still hold distinct live claims.
    assert {row[2] for row in _rows(client, "status='claimed'")} == set(SESSION_OWNERS[1:])


def test_stale_owner_is_fenced_after_recovery_by_another_session(tmp_path):
    """After lease expiry a different session may recover, fencing the old token."""
    path = tmp_path / f"outbox-r29-recover-{uuid.uuid4().hex}.sqlite"
    client = oc.OutboxClient(db_path=str(path), api_url="", auto_flush=False)
    conn = _connect(path)
    try:
        conn.execute(
            "INSERT INTO outbox (outbox_id, event_id, payload_json, "
            "attempt_count, status, created_at, claim_owner) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("rec-0", "evt-rec", "{}", 0, "pending",
             "2026-11-27T00:00:00+00:00", None),
        )
        conn.commit()
    finally:
        conn.close()

    stale = client._claim_next(owner=SESSION_OWNERS[0])
    assert stale is not None and stale.claim_owner == SESSION_OWNERS[0]

    # Expire the lease, then let a different session renew/recover the row.
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE outbox SET claimed_at=? WHERE outbox_id='rec-0'",
            ("2026-11-20T00:00:00+00:00",),
        )
        conn.commit()
    finally:
        conn.close()

    renewed = client._claim_next(owner=SESSION_OWNERS[2])
    assert renewed is not None and renewed.claim_owner == SESSION_OWNERS[2]
    assert _rows(client, "outbox_id='rec-0'") == [
        ("rec-0", "claimed", SESSION_OWNERS[2])
    ]
    # The pre-recovery owner's token can no longer release the row.
    assert client._release(stale, "delivered", 1) is False
    assert client._release(renewed, "delivered", 1) is True
    assert _rows(client, "outbox_id='rec-0'") == [("rec-0", "delivered", None)]
