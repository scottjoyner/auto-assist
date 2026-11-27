"""R30-4: lease-expiry recovery of a session-owned claim, with quarantine holds intact.

Companion to ``test_session_owner_lifecycle_r29.py`` (branch
``agent/session-owner-lifecycle-r29-20261126``). This module adds the one case
that lifecycle slice did not isolate: once a claimed row's lease has expired, a
*different* session owner may recover the row, but the recovery must fence the
original owner's token, and an operator ``quarantine:`` marker must stay
unclaimable through that same expiry path.

Containment: every fixture writes to a fresh SQLite file created by pytest's
``tmp_path`` (the harness pins ``TMPDIR`` to the SSD scratch root), never to the
live ``~/.assistx_outbox.db``. The fixture asserts that containment when
``TMPDIR`` is set. No production code, service, compose file or live DB is
changed; no MCP connection is made and no credentials/env snapshots are read.
The session labels are synthetic stand-ins, not real session ids.

The assertions mirror the shipped protocol in ``assistx.outbox_client``:
``_claim_next`` treats ``status='claimed' AND claimed_at < stale_cutoff`` as
deliverable, applies ``_QUARANTINE_CLAIM_GUARD`` to both its SELECT and guarded
UPDATE, and overwrites ``claim_owner`` with the recovering owner's token;
``_release`` only accepts a write whose ``claim_owner`` still matches; and
``queue_health`` derives ``quarantined``/``claimable_pending`` from those same
predicates on a read-only connection.
"""

import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import assistx.outbox_client as oc

OWNER_A = "sess-r30-original"
OWNER_B = "sess-r30-recoverer"
OWNER_C = "sess-r30-third"

QUARANTINE_MARKER = "quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126"

# Pin health evaluation so staleness never depends on the wall clock.
NOW = datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC)

# Far in the past relative to any plausible clock/lease: guarantees the row is
# past ``stale_cutoff`` without depending on the default 90 s lease.
EXPIRED_AT = (datetime(2026, 11, 27, tzinfo=UTC) - timedelta(days=365)).isoformat()


def _connect(path):
    return sqlite3.connect(str(path))


@pytest.fixture
def expiry_client(tmp_path):
    """A fresh temp-file outbox under TMPDIR (the SSD scratch root)."""
    path = tmp_path / f"outbox-r30-expiry-{uuid.uuid4().hex}.sqlite"
    # Containment witness: when the harness pins TMPDIR, tmp_path must resolve
    # under it. This never hard-requires TMPDIR (the test stays portable).
    ssd_root = os.environ.get("TMPDIR")
    if ssd_root and os.path.isdir(ssd_root):
        assert os.path.commonpath([os.path.realpath(str(path)), os.path.realpath(ssd_root)]) == os.path.realpath(
            ssd_root
        )
    client = oc.OutboxClient(db_path=str(path), api_url="", auto_flush=False)
    return client


def _insert(client, outbox_id, *, status="pending", claim_owner=None, claimed_at=None, created_at=None):
    conn = _connect(client.db_path)
    try:
        conn.execute(
            "INSERT INTO outbox (outbox_id, event_id, payload_json, attempt_count, "
            "status, created_at, claim_owner, claimed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                outbox_id,
                f"evt-{outbox_id}",
                "{}",
                0,
                status,
                created_at or "2026-11-27T11:00:00+00:00",
                claim_owner,
                claimed_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _rows(client, where="1=1", params=()):
    conn = _connect(client.db_path)
    try:
        return conn.execute(
            "SELECT outbox_id, status, claim_owner FROM outbox WHERE " + where,
            params,
        ).fetchall()
    finally:
        conn.close()


def _expire(client, outbox_id):
    """Force a lease expiry by backdating claimed_at (never touches live DBs)."""
    conn = _connect(client.db_path)
    try:
        conn.execute(
            "UPDATE outbox SET claimed_at=? WHERE outbox_id=?",
            (EXPIRED_AT, outbox_id),
        )
        conn.commit()
    finally:
        conn.close()


def _forged(entry, owner):
    """Copy a claimed entry but present a different owner's release token."""
    return oc.OutboxEntry(
        outbox_id=entry.outbox_id,
        event_id=entry.event_id,
        payload_json=entry.payload_json,
        attempt_count=entry.attempt_count,
        status="claimed",
        created_at=entry.created_at,
        claim_owner=owner,
        claimed_at=entry.claimed_at,
    )


def test_expired_lease_recovered_by_different_owner_fences_original_token(expiry_client):
    """A second owner may recover an expired claim; the first token is fenced."""
    client = expiry_client
    _insert(client, "rec-0")
    assert _rows(client, "outbox_id='rec-0'") == [("rec-0", "pending", None)]

    original = client._claim_next(owner=OWNER_A)
    assert original is not None
    assert (original.outbox_id, original.status, original.claim_owner) == ("rec-0", "claimed", OWNER_A)
    assert _rows(client, "outbox_id='rec-0'") == [("rec-0", "claimed", OWNER_A)]

    # Lease lapses: the row is still 'claimed', just stale.
    _expire(client, "rec-0")
    assert _rows(client, "outbox_id='rec-0'") == [("rec-0", "claimed", OWNER_A)]

    # A different session recovers it and its token replaces the old one.
    recovered = client._claim_next(owner=OWNER_B)
    assert recovered is not None
    assert (recovered.outbox_id, recovered.status, recovered.claim_owner) == ("rec-0", "claimed", OWNER_B)
    assert _rows(client, "outbox_id='rec-0'") == [("rec-0", "claimed", OWNER_B)]

    # Original and third-party tokens are both fenced; the row is untouched.
    assert client._release(original, "delivered", 1) is False
    assert client._release(_forged(recovered, OWNER_C), "delivered", 1) is False
    assert _rows(client, "outbox_id='rec-0'") == [("rec-0", "claimed", OWNER_B)]

    # Only the recovering owner's token can finalize the row.
    assert client._release(recovered, "delivered", 1) is True
    assert _rows(client, "outbox_id='rec-0'") == [("rec-0", "delivered", None)]


def test_quarantine_marked_row_never_becomes_claimable_through_expiry(expiry_client):
    """Even an expired, quarantine-marked claim is never recovered by another owner.

    The two held rows are deliberately the *oldest* deliverable-looking rows:
    without the quarantine guard on both SELECT and UPDATE, the recovery claim
    would pick one of them rather than the recoverable row.
    """
    client = expiry_client
    _insert(
        client, "q-pending", status="pending", claim_owner=QUARANTINE_MARKER, created_at="2026-11-27T00:00:01+00:00"
    )
    _insert(
        client,
        "q-expired",
        status="claimed",
        claim_owner=QUARANTINE_MARKER,
        claimed_at=EXPIRED_AT,
        created_at="2026-11-27T00:00:02+00:00",
    )
    _insert(client, "recoverable", created_at="2026-11-27T00:00:03+00:00")
    _expire(client, "recoverable")

    first = client._claim_next(owner=OWNER_A)
    assert first is not None and first.outbox_id == "recoverable"
    # Every later claim (including other session owners) finds nothing.
    assert client._claim_next(owner=OWNER_B) is None
    assert client._claim_next(owner=OWNER_C) is None

    # Both quarantine rows keep their marker, status and expired timestamp:
    # expiry recovery does not launder an operator hold into a live claim.
    assert sorted(_rows(client, "outbox_id LIKE 'q-%'")) == [
        ("q-expired", "claimed", QUARANTINE_MARKER),
        ("q-pending", "pending", QUARANTINE_MARKER),
    ]
    conn = _connect(client.db_path)
    try:
        assert conn.execute("SELECT claimed_at FROM outbox WHERE outbox_id='q-expired'").fetchone() == (EXPIRED_AT,)
    finally:
        conn.close()

    # Health counts track the holds by claim_owner, independent of status, and
    # claimable_pending counts only pending rows without a quarantine marker.
    health = client.queue_health(now=NOW)
    assert health["quarantined"] == 2
    assert health["claimable_pending"] == 0
    assert health["pending"] == 1  # only q-pending is still 'pending'
    assert health["claimed"] == 2  # q-expired + the recovered 'recoverable'
    # No deliverable row remains, so a recovery sweep cannot touch the holds.
    assert client._claim_next() is None


def test_health_counters_consistent_across_expiry_recovery(expiry_client):
    """claimable_pending/quarantined stay exact at every recovery checkpoint."""
    client = expiry_client
    _insert(client, "held", claim_owner=QUARANTINE_MARKER)
    _insert(client, "rec-0")

    def checkpoint(expected_pending, expected_claimable, expected_claimed=0, expected_delivered=0):
        health = client.queue_health(now=NOW)
        assert health["status"] == "ok" and health["ok"] is True
        assert health["quarantined"] == 1
        assert health["claimable_pending"] == expected_claimable
        assert health["pending"] == expected_pending
        assert health["claimed"] == expected_claimed
        assert health["delivered"] == expected_delivered
        # The additive counters always reconcile with the pending bucket, and
        # the row total is invariant across claim / recovery / release.
        assert health["claimable_pending"] + health["quarantined"] == health["pending"]
        assert health["total"] == 2
        # The held row is never rewritten by a claim or a health probe.
        assert _rows(client, "outbox_id='held'") == [("held", "pending", QUARANTINE_MARKER)]

    checkpoint(expected_pending=2, expected_claimable=1)

    original = client._claim_next(owner=OWNER_A)
    assert original is not None and original.outbox_id == "rec-0"
    checkpoint(expected_pending=1, expected_claimable=0, expected_claimed=1)

    _expire(client, "rec-0")
    recovered = client._claim_next(owner=OWNER_B)
    assert recovered is not None and recovered.claim_owner == OWNER_B
    checkpoint(expected_pending=1, expected_claimable=0, expected_claimed=1)

    # Fencing the stale owner does not perturb the counters.
    assert client._release(original, "delivered", 1) is False
    checkpoint(expected_pending=1, expected_claimable=0, expected_claimed=1)

    # The recovering owner's release moves one row to 'delivered'; the hold is
    # still pending, still quarantined, and still unclaimable.
    assert client._release(recovered, "delivered", 1) is True
    checkpoint(expected_pending=1, expected_claimable=0, expected_delivered=1)
    assert client._claim_next(owner=OWNER_C) is None
