"""Hardening contract tests for the R23 optional ``claim_owner`` validator (r25).

Offline only: every fixture is a temp SQLite file under pytest's ``tmp_path``
(TMPDIR is on the SSD), or in-memory SQLite. No live DB, no credentials, no
service/compose change and no capacity claim. The R23 validator is treated as a
contract here: rejection must happen *before any write*, and a valid
session-shaped label must remain an enforceable fence across lease recovery.
"""

import sqlite3
import uuid

import pytest

import assistx.outbox_client as oc

VALID_SESSION = "sess-" + "a" * 32
OTHER_SESSION = "sess-" + "b" * 32
EXPIRED_LEASE_AT = "2026-11-20T00:00:00+00:00"


@pytest.fixture
def seeded_client(tmp_path):
    """Fresh temp-file outbox with exactly one pending row."""
    path = tmp_path / f"outbox-r25-{uuid.uuid4().hex}.sqlite"
    client = oc.OutboxClient(db_path=str(path), api_url="", auto_flush=False)
    client.enqueue({"event_id": "r25-owner-hardening"})
    return client


def _row(client):
    conn = sqlite3.connect(client.db_path)
    try:
        return conn.execute("SELECT status, claim_owner FROM outbox").fetchone()
    finally:
        conn.close()


def _expire_lease(client):
    conn = sqlite3.connect(client.db_path)
    try:
        conn.execute("UPDATE outbox SET claimed_at=?", (EXPIRED_LEASE_AT,))
        conn.commit()
    finally:
        conn.close()


# Each shape must be refused by the R23 validator before the claim UPDATE runs.
REJECTED_SHAPES = [
    ("whitespace-only spaces", "   "),
    ("whitespace-only mixed", " \t "),
    ("leading space", " " + VALID_SESSION),
    ("trailing space", VALID_SESSION + " "),
    ("leading NBSP U+00A0", "\u00a0" + VALID_SESSION),
    ("trailing NBSP U+00A0", VALID_SESSION + "\u00a0"),
    ("NUL escape", "sess-\x00abc"),
    ("ESC escape", "sess-\x1babc"),
    ("DEL escape", "sess-\x7fabc"),
    ("carriage return", "sess-\rabc"),
    ("line feed", "sess-\nabc"),
    ("U+2028 line separator", "sess-\u2028abc"),
    ("U+2029 paragraph separator", "sess-\u2029abc"),
    ("zero-width space U+200B", "sess-\u200babc"),
    ("empty string", ""),
    ("overlong 129", "s" * (oc.MAX_CLAIM_OWNER_LEN + 1)),
    ("overlong 256", "s" * 256),
    ("quarantine prefix", "QUARANTINE:r25-hold"),
]


@pytest.mark.parametrize("label,owner", REJECTED_SHAPES, ids=[r[0] for r in REJECTED_SHAPES])
def test_rejected_owner_writes_nothing(seeded_client, label, owner):
    """Every malformed owner fails closed: the row stays pending and unclaimed."""
    with pytest.raises(ValueError):
        seeded_client._claim_next(owner=owner)
    assert _row(seeded_client) == ("pending", None), label
    # The failed claim did not corrupt the queue: an ordinary claim still works.
    assert seeded_client._claim_next() is not None
    assert _row(seeded_client)[0] == "claimed"


def test_length_boundaries_are_exact(seeded_client):
    """128 characters is accepted; 129 is not (len check is inclusive at the top)."""
    client = seeded_client
    with pytest.raises(ValueError, match="128"):
        client._claim_next(owner="s" * (oc.MAX_CLAIM_OWNER_LEN + 1))
    assert _row(client) == ("pending", None)

    owner = "s" * oc.MAX_CLAIM_OWNER_LEN
    entry = client._claim_next(owner=owner)
    assert entry is not None and entry.claim_owner == owner
    assert _row(client) == ("claimed", owner)


def test_valid_session_shaped_owner_is_accepted(seeded_client):
    """A short printable session-shaped id is stored verbatim and returned."""
    entry = seeded_client._claim_next(owner=VALID_SESSION)
    assert entry is not None
    assert entry.claim_owner == VALID_SESSION
    assert _row(seeded_client) == ("claimed", VALID_SESSION)


def test_valid_owner_survives_lease_recovery(seeded_client):
    """After lease expiry a new valid owner recovers the row once and fences the old one."""
    client = seeded_client
    first = client._claim_next(owner=VALID_SESSION)
    assert first is not None and first.claim_owner == VALID_SESSION

    _expire_lease(client)
    recovered = client._claim_next(owner=OTHER_SESSION)
    assert recovered is not None and recovered.claim_owner == OTHER_SESSION
    assert _row(client) == ("claimed", OTHER_SESSION)

    # The label earned at recovery is the fence: stale owner cannot release it.
    assert client._release(first, "delivered", 1) is False
    assert _row(client) == ("claimed", OTHER_SESSION)
    assert client._release(recovered, "delivered", 1) is True
    assert _row(client) == ("delivered", None)


def test_same_owner_can_reclaim_its_expired_lease(seeded_client):
    """Recovery is not blocked when the same valid id reclaims its own row."""
    client = seeded_client
    first = client._claim_next(owner=VALID_SESSION)
    assert first is not None

    _expire_lease(client)
    again = client._claim_next(owner=VALID_SESSION)
    assert again is not None and again.claim_owner == VALID_SESSION
    assert _row(client) == ("claimed", VALID_SESSION)
    assert client._release(again, "delivered", 1) is True


def test_unicode_quarantine_lookalike_is_not_a_spoof(seeded_client):
    """No bypass: a Unicode lookalike accepted by the validator is not a SQLite hold.

    The R23 prefix check is case-insensitive for ASCII; SQLite ``LIKE`` folds
    ASCII case only. A non-ASCII lookalike therefore cannot be mistaken for an
    operator ``quarantine:%`` hold, so accepting it does not evade the drain
    guard. This is the evidence for making no production change in r25.
    """
    lookalike = "QUARANT\u0130NE:r25-lookalike"  # Turkish dotted capital I
    conn = sqlite3.connect(":memory:")
    try:
        matched = conn.execute("SELECT 1 WHERE ? LIKE 'quarantine:%'", (lookalike,)).fetchone()
    finally:
        conn.close()
    assert matched is None

    entry = seeded_client._claim_next(owner=lookalike)
    assert entry is not None and entry.claim_owner == lookalike
    assert _row(seeded_client) == ("claimed", lookalike)
