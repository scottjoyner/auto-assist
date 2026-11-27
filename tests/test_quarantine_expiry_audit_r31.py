"""R31-4: quarantine expiry audit -- 14 attempt-9 holds survive lease sweeps.

Companion to ``test_owner_token_expiry_r30.py`` (branch
``agent/owner-token-expiry-r30-20261126``). Where R30 isolated one hold and one
recovery, this slice audits the whole operator quarantine set left by the
2026-11 incident: 14 rows frozen at ``attempt_count=9`` by the
``stale-attempt9-over-maxattempts5`` reconciliation. It asserts that a lease
expiry/recovery sweep -- the ``_claim_next`` path that reclaims stale ``claimed``
rows -- never strips an operator marker, never makes a held row claimable, and
that ``quarantined`` / ``claimable_pending`` / ``pending`` reconcile before and
after the sweep. A final case checks that a malformed quarantine-like label
cannot spoof a hold unless it carries the exact ``quarantine:`` prefix.

Containment: every fixture writes to a fresh SQLite file created by pytest's
``tmp_path`` (the harness pins ``TMPDIR`` to the SSD scratch root), never to the
live ``~/.assistx_outbox.db``. The fixture asserts that containment when TMPDIR
is set. No production code, service, compose file or live DB is changed; no MCP
connection is made and no credentials/env snapshots are read. The session labels
below are synthetic stand-ins, not real session ids.

The assertions mirror the shipped protocol in ``assistx.outbox_client``:
``_claim_next`` applies ``_QUARANTINE_CLAIM_GUARD`` to both its SELECT and its
guarded UPDATE, ``_release`` fences on the exact ``claim_owner`` token, and
``queue_health`` derives ``quarantined``/``claimable_pending`` from those same
predicates on a read-only connection.
"""

import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import assistx.outbox_client as oc

OWNER_A = "sess-r31-auditor"
OWNER_B = "sess-r31-recoverer"
OWNER_C = "sess-r31-third"

# Verbatim R4 reconciliation hold label (round4-session1 report).
QUARANTINE_MARKER = "quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126"
ATTEMPT_COUNT = 9
N_HOLDS = 14
N_PENDING_HOLDS = 7  # the other 7 are 'claimed' with an already-expired lease

# Pin health evaluation so staleness never depends on the wall clock.
NOW = datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC)
FRESH_AT = "2026-11-27T11:00:00+00:00"
# Far in the past relative to any plausible clock/lease: guarantees the row is
# past ``stale_cutoff`` without depending on the default 90 s lease.
EXPIRED_AT = (datetime(2026, 11, 27, tzinfo=UTC) - timedelta(days=365)).isoformat()

NORMAL_IDS = ("normal-0", "normal-1")


def _hold_ids():
    return [f"q-{i:02d}" for i in range(N_HOLDS)]


@pytest.fixture
def audit_client(tmp_path):
    """A fresh temp-file outbox under TMPDIR (the SSD scratch root)."""
    path = tmp_path / f"outbox-r31-audit-{uuid.uuid4().hex}.sqlite"
    # Containment witness: when the harness pins TMPDIR, tmp_path must resolve
    # under it. This never hard-requires TMPDIR (the test stays portable).
    ssd_root = os.environ.get("TMPDIR")
    if ssd_root and os.path.isdir(ssd_root):
        assert os.path.commonpath([os.path.realpath(str(path)), os.path.realpath(ssd_root)]) == os.path.realpath(
            ssd_root
        )
    return oc.OutboxClient(db_path=str(path), api_url="", auto_flush=False)


def _insert(
    client,
    outbox_id,
    *,
    status="pending",
    attempt_count=0,
    claim_owner=None,
    claimed_at=None,
    created_at=None,
):
    conn = sqlite3.connect(str(client.db_path))
    try:
        conn.execute(
            "INSERT INTO outbox (outbox_id, event_id, payload_json, attempt_count, "
            "status, created_at, claim_owner, claimed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                outbox_id,
                f"evt-{outbox_id}",
                "{}",
                attempt_count,
                status,
                created_at or FRESH_AT,
                claim_owner,
                claimed_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _rows(client, where="1=1", params=()):
    conn = sqlite3.connect(str(client.db_path))
    try:
        return conn.execute(
            "SELECT outbox_id, status, attempt_count, claim_owner, claimed_at FROM outbox WHERE " + where,
            params,
        ).fetchall()
    finally:
        conn.close()


def _seed_holds(client):
    """14 attempt-9 holds: 7 pending, 7 claimed with an expired lease, +2 normal."""
    for i, outbox_id in enumerate(_hold_ids()):
        if i < N_PENDING_HOLDS:
            _insert(
                client,
                outbox_id,
                attempt_count=ATTEMPT_COUNT,
                claim_owner=QUARANTINE_MARKER,
                created_at=f"2026-11-27T00:00:{i:02d}+00:00",
            )
        else:
            _insert(
                client,
                outbox_id,
                status="claimed",
                attempt_count=ATTEMPT_COUNT,
                claim_owner=QUARANTINE_MARKER,
                claimed_at=EXPIRED_AT,
                created_at=f"2026-11-27T00:01:{i - N_PENDING_HOLDS:02d}+00:00",
            )
    for i, outbox_id in enumerate(NORMAL_IDS):
        _insert(client, outbox_id, created_at=f"2026-11-27T00:02:{i:02d}+00:00")


def _hold_snapshot(client):
    ids = _hold_ids()
    marks = ",".join("?" * len(ids))
    return _rows(client, f"outbox_id IN ({marks}) ORDER BY outbox_id", tuple(ids))


def _assert_holds_intact(client):
    snapshot = _hold_snapshot(client)
    assert len(snapshot) == N_HOLDS
    for outbox_id, status, attempt, owner, claimed_at in snapshot:
        assert outbox_id.startswith("q-")
        assert status in ("pending", "claimed")
        assert attempt == ATTEMPT_COUNT
        assert owner == QUARANTINE_MARKER
        # Expired holds keep their stale timestamp; pending holds stay unclaimed.
        if status == "claimed":
            assert claimed_at == EXPIRED_AT
        else:
            assert claimed_at is None
    return snapshot


def _sweep(client, owner):
    """Drain every deliverable row, releasing each; return the claimed ids."""
    claimed = []
    while True:
        entry = client._claim_next(owner=owner)
        if entry is None:
            break
        claimed.append(entry.outbox_id)
        assert client._release(entry, "delivered", entry.attempt_count + 1) is True
    return claimed


def _health(client):
    health = client.queue_health(now=NOW)
    assert health["ok"] is True
    return health


def test_14_attempt9_holds_are_never_claimable_across_recovery_sweep(audit_client):
    """A full lease-expiry/recovery drain touches only the two normal rows."""
    client = audit_client
    _seed_holds(client)
    before = _assert_holds_intact(client)

    claimed = _sweep(client, OWNER_A)
    assert sorted(claimed) == sorted(NORMAL_IDS)
    assert not (set(claimed) & set(_hold_ids()))

    after = _assert_holds_intact(client)
    assert after == before  # every marker, status, attempt and timestamp intact
    # A second sweep is inert: no hold has been laundered into the deliverable set.
    assert _sweep(client, OWNER_B) == []
    assert _sweep(client, OWNER_C) == []
    assert _assert_holds_intact(client) == before


def test_expired_hold_never_recovered_by_another_owner(audit_client):
    """The 7 stale 'claimed' holds look deliverable but the guard still skips them."""
    client = audit_client
    _seed_holds(client)
    expired_ids = _hold_ids()[N_PENDING_HOLDS:]

    # Force the recovery path with a different session owner; only normals come
    # back, and the expired holds keep the operator marker and stale timestamp.
    first = client._claim_next(owner=OWNER_B)
    assert first is not None and first.outbox_id in NORMAL_IDS
    second = client._claim_next(owner=OWNER_C)
    assert second is not None and second.outbox_id in NORMAL_IDS
    assert client._claim_next(owner=OWNER_A) is None

    held = _rows(client, "outbox_id IN (" + ",".join("?" * len(expired_ids)) + ")", tuple(expired_ids))
    assert len(held) == N_PENDING_HOLDS
    for outbox_id, status, attempt, owner, claimed_at in held:
        assert outbox_id in expired_ids
        assert (status, attempt, owner, claimed_at) == ("claimed", ATTEMPT_COUNT, QUARANTINE_MARKER, EXPIRED_AT)
    _assert_holds_intact(client)


def test_health_counters_reconcile_before_and_after_sweep(audit_client):
    """quarantined / claimable_pending / pending stay exact on both sides."""
    client = audit_client
    _seed_holds(client)

    def check(expected_claimable, expected_pending, expected_delivered):
        health = _health(client)
        snapshot = _assert_holds_intact(client)
        quarantined_pending = sum(1 for _, status, *_ in snapshot if status == "pending")
        assert health["quarantined"] == N_HOLDS
        assert health["claimable_pending"] == expected_claimable
        assert health["pending"] == expected_pending
        assert health["delivered"] == expected_delivered
        assert health["total"] == N_HOLDS + len(NORMAL_IDS)
        # Additive reconciliation: pending splits into held-pending and claimable.
        assert quarantined_pending + health["claimable_pending"] == health["pending"]
        assert health["claimable_pending"] <= health["pending"]

    # Before: 7 held-pending + 2 normal pending = 9 pending; only 2 claimable.
    check(expected_claimable=2, expected_pending=9, expected_delivered=0)
    assert _sweep(client, OWNER_B) == list(NORMAL_IDS)
    # After: normals delivered; 7 held-pending + 7 expired-held-claimed remain.
    check(expected_claimable=0, expected_pending=7, expected_delivered=len(NORMAL_IDS))
    assert _sweep(client, OWNER_C) == []
    check(expected_claimable=0, expected_pending=7, expected_delivered=len(NORMAL_IDS))


def test_exact_prefix_required_for_a_quarantine_hold(audit_client):
    """Near-miss labels cannot spoof a hold; only 'quarantine:' is excluded."""
    assert oc._QUARANTINE_OWNER_PREFIX == "quarantine:"
    assert "LIKE 'quarantine:%'" in oc._QUARANTINE_CLAIM_GUARD

    near_misses = (
        "quarantine",  # no colon
        "not-quarantine:stale",  # prefix embedded, not leading
        "xquarantine:stale",  # leading character before the prefix
        "quarantine :stale",  # space before the colon
        " quarantine:stale",  # leading whitespace
    )

    # Pure in-memory check of the production guard pattern: every near miss is
    # treated as claimable; only the exact-prefix marker is held.
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE outbox (claim_owner TEXT)")
        conn.executemany(
            "INSERT INTO outbox VALUES (?)",
            [(label,) for label in near_misses] + [(QUARANTINE_MARKER,), (None,)],
        )
        guarded = {row[0] for row in conn.execute(f"SELECT claim_owner FROM outbox WHERE {oc._QUARANTINE_CLAIM_GUARD}")}
        assert QUARANTINE_MARKER not in guarded
        assert None in guarded
        assert set(near_misses) <= guarded
    finally:
        conn.close()

    # Same contract on a real client: near misses are claimable and are NOT
    # counted as quarantined; the exact marker is skipped and counted.
    client = audit_client
    for i, label in enumerate(near_misses):
        _insert(client, f"near-{i}", claim_owner=label, created_at=f"2026-11-27T00:03:{i:02d}+00:00")
    _insert(client, "exact", attempt_count=ATTEMPT_COUNT, claim_owner=QUARANTINE_MARKER)

    health = _health(client)
    assert health["quarantined"] == 1
    assert health["claimable_pending"] == len(near_misses)
    assert health["pending"] == len(near_misses) + 1

    claimed = _sweep(client, OWNER_A)
    assert sorted(claimed) == [f"near-{i}" for i in range(len(near_misses))]
    assert _rows(client, "outbox_id='exact'")[0][1:4] == ("pending", ATTEMPT_COUNT, QUARANTINE_MARKER)
    after = _health(client)
    assert after["quarantined"] == 1
    assert after["claimable_pending"] == 0
    assert after["delivered"] == len(near_misses)


@pytest.mark.parametrize("spoof", [QUARANTINE_MARKER, "QUARANTINE:hold", "quarantine:anything"])
def test_claim_owner_cannot_mint_a_quarantine_prefix(spoof):
    """Callers cannot create a hold by choosing a quarantine-shaped owner label."""
    with pytest.raises(ValueError):
        oc._validate_claim_owner(spoof)
    assert oc._validate_claim_owner(OWNER_A) == OWNER_A
