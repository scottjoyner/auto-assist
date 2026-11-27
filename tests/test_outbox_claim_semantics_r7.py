"""Read-only outbox-health semantics: quarantine LABEL vs ENFORCED claim (r7).

Round 4 wrote a reversible ``claim_owner='quarantine:...'`` label onto 14 stale
attempt-9 rows and recorded the caveat that it is NOT an enforced claim lock:
the claim protocol keys off ``status``/``claimed_at``, never off ``claim_owner``.
The health signal must key off ``status`` for the same reason, so a quarantined
row is never reported as actively claimed or as drained. All rows here live in
pytest ``tmp_path`` SQLite files; the live ``~/.assistx_outbox.db`` is never
opened.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

from assistx.outbox_client import OutboxClient, read_queue_health

QUARANTINE_OWNER = "quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126"
ENFORCED_TOKEN = "flusher-claim-7d2e9a4b"


def _seed(db_path, *, rows):
    """Create the production outbox schema in a temp DB and insert raw rows.

    Each row is (outbox_id, status, claim_owner, claimed_at, attempt_count,
    created_at). Inserting directly keeps the fixture independent of the
    claim/purge write paths.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE outbox (
                outbox_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                attempt_count INTEGER DEFAULT 0,
                last_attempt_at TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                claim_owner TEXT,
                claimed_at TEXT
            )
            """
        )
        for outbox_id, status, owner, claimed_at, attempt, created_at in rows:
            conn.execute(
                "INSERT INTO outbox (outbox_id, event_id, payload_json, "
                "attempt_count, last_attempt_at, status, created_at, "
                "claim_owner, claimed_at) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?)",
                (
                    outbox_id,
                    f"evt-{outbox_id}",
                    "{}",
                    attempt,
                    status,
                    created_at,
                    owner,
                    claimed_at,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def test_quarantine_label_is_neither_a_claim_nor_a_drain(tmp_path):
    now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    fresh = now.isoformat()

    # DB A: the exact Round-4 shape -- a stale attempt-9 row carrying only the
    # reversible quarantine label, with no enforced claim and no drain.
    label_only = tmp_path / "label_only.sqlite"
    _seed(
        label_only,
        rows=[("q-1", "pending", QUARANTINE_OWNER, None, 9, fresh)],
    )
    before = label_only.read_bytes()
    label_health = read_queue_health(str(label_only), now=now)
    assert label_only.read_bytes() == before  # probe is strictly read-only

    assert label_health["pending"] == 1
    assert label_health["claimed"] == 0  # label is not an active claim
    assert label_health["dead"] == 0  # label is not a drain
    assert label_health["delivered"] == 0  # label is not a drain
    assert label_health["total"] == 1
    assert label_health["ok"] is True
    assert label_health["status"] == "ok"

    # DB B: one quarantine LABEL (pending) beside one genuine ENFORCED claim
    # (status='claimed', token owner, claimed_at set). The health signal must
    # count exactly the enforced one as claimed -- it keys off status, never
    # off the mere presence of a claim_owner value.
    mixed = tmp_path / "mixed.sqlite"
    _seed(
        mixed,
        rows=[
            ("q-2", "pending", QUARANTINE_OWNER, None, 9, fresh),
            ("c-1", "claimed", ENFORCED_TOKEN, fresh, 0, fresh),
        ],
    )
    mixed_health = read_queue_health(str(mixed), now=now)

    assert mixed_health["claimed"] == 1  # not 2: the label is not a claim
    assert mixed_health["pending"] == 1  # the labeled row stays pending
    assert mixed_health["dead"] == 0
    assert mixed_health["delivered"] == 0
    assert mixed_health["total"] == 2
    assert mixed_health["ok"] is True

    # The label is not an enforced lock either: a configured flusher can still
    # claim the quarantined row (the Round-4 caveat), after which health
    # legitimately reports it claimed -- proving the two states are distinct.
    # This only writes to the temp DB, never to the live outbox.
    client = OutboxClient(db_path=str(label_only), api_url="", auto_flush=False)
    claimed = client._claim_next()
    assert claimed is not None
    assert claimed.outbox_id == "q-1"
    assert claimed.claim_owner != QUARANTINE_OWNER
    after_claim_health = read_queue_health(str(label_only), now=now)
    assert after_claim_health["claimed"] == 1
    assert after_claim_health["pending"] == 0
    assert after_claim_health["dead"] == 0
    assert after_claim_health["delivered"] == 0
