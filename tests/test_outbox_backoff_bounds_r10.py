"""Round-10 tests: the AssistX outbox worker's exponential retry/backoff bound.

``OutboxClient.flush`` must not hammer a destination that is failing: a row
that is still inside its backoff window is released untouched, and only a row
whose window has elapsed is delivered. The curve lives in the pure helper
``OutboxClient._backoff_remaining_s`` — ``base * 2**attempt``, capped at
``retry_max_s``, with the exponent itself capped at 16.

Why this is new (not a redo of prior rounds):

* r3 pinned ``read_queue_health`` buckets (depth / staleness read-only view).
* r4 quarantined stale over-attempt rows (DB state, owner-gated live outbox).
* r7 pinned the atomic claim / lease / stale-recovery protocol.
* r6 pinned the PR-ready drain surface.

None of those touched the *delay curve* itself. ``tests/test_outbox_client.py``
only sets an already-old ``last_attempt_at`` to skip the window; it never pins
the base, the doubling, the max cap, the exponent cap, or the flush-time
deferral. These tests exercise only a temp SQLite file and a fake ``_deliver``:
no live ``~/.assistx_outbox.db``, no HTTP, no credentials, no services.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from assistx.outbox_client import OutboxClient


def _client(tmp_path, **kwargs):
    return OutboxClient(
        db_path=str(tmp_path / "backoff.sqlite"),
        api_url="http://example.invalid",  # non-empty so flush does not early-return
        auto_flush=False,
        **kwargs,
    )


def _entry(attempt_count, last_attempt_at):
    """Minimal stand-in with only the attributes _backoff_remaining_s reads."""
    from assistx.outbox_client import OutboxEntry

    return OutboxEntry(
        outbox_id="o1",
        event_id="e1",
        payload_json="{}",
        attempt_count=attempt_count,
        last_attempt_at=last_attempt_at,
    )


def _iso(dt):
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Curve shape (pure helper, no DB/IO)
# ---------------------------------------------------------------------------


def test_backoff_is_zero_before_the_first_attempt(tmp_path):
    client = _client(tmp_path)
    now = datetime.now(UTC)
    # A freshly staged row (attempt_count 0) is always due immediately, even
    # if a timestamp is present for some reason.
    assert client._backoff_remaining_s(_entry(0, _iso(now)), now) == 0.0


def test_backoff_is_zero_when_last_attempt_is_missing_or_malformed(tmp_path):
    client = _client(tmp_path)
    now = datetime.now(UTC)
    assert client._backoff_remaining_s(_entry(3, None), now) == 0.0
    assert client._backoff_remaining_s(_entry(3, "not-a-timestamp"), now) == 0.0


def test_backoff_starts_at_base_times_two(tmp_path):
    client = _client(tmp_path, retry_base_s=5, retry_max_s=300)
    now = datetime.now(UTC)
    # delay = base * 2**attempt = 5 * 2**1 = 10 s at the moment of failure.
    assert client._backoff_remaining_s(_entry(1, _iso(now)), now) == 10.0


def test_backoff_doubles_with_each_attempt(tmp_path):
    client = _client(tmp_path, retry_base_s=5, retry_max_s=10000)
    now = datetime.now(UTC)
    delays = [client._backoff_remaining_s(_entry(a, _iso(now)), now) for a in (1, 2, 3, 4)]
    assert delays == [10.0, 20.0, 40.0, 80.0]


def test_backoff_remaining_decreases_and_floors_at_zero(tmp_path):
    client = _client(tmp_path, retry_base_s=5, retry_max_s=300)
    t0 = datetime.now(UTC)
    last = _iso(t0)
    values = [client._backoff_remaining_s(_entry(1, last), t0 + timedelta(seconds=sec)) for sec in (0, 4, 10, 50)]
    assert values[0] == 10.0
    assert values[1] == 6.0
    assert values[2] == 0.0
    assert values[3] == 0.0  # never negative long after the window closes


def test_backoff_is_capped_at_retry_max(tmp_path):
    client = _client(tmp_path, retry_base_s=5, retry_max_s=300)
    now = datetime.now(UTC)
    # 5 * 2**10 = 5120 s, well over the 300 s ceiling.
    assert client._backoff_remaining_s(_entry(10, _iso(now)), now) == 300.0


def test_backoff_exponent_is_capped_to_avoid_unbounded_growth(tmp_path):
    # A very high ceiling isolates the exponent guard from the max guard.
    client = _client(tmp_path, retry_base_s=1, retry_max_s=1_000_000)
    now = datetime.now(UTC)
    at_cap = client._backoff_remaining_s(_entry(16, _iso(now)), now)
    beyond_cap = client._backoff_remaining_s(_entry(999, _iso(now)), now)
    assert at_cap == 65536.0  # 1 * 2**16
    assert beyond_cap == at_cap  # attempt 999 must not raise 2**999


def test_backoff_uses_instance_config_not_module_defaults(tmp_path):
    client = _client(tmp_path, retry_base_s=1, retry_max_s=8)
    now = datetime.now(UTC)
    # base * 2**4 = 16, capped at retry_max_s=8.
    assert client._backoff_remaining_s(_entry(4, _iso(now)), now) == 8.0


# ---------------------------------------------------------------------------
# Real failure surface: flush defers a row still inside its backoff window
# ---------------------------------------------------------------------------


def _stage_failed_row(client, attempt_count, last_attempt_at):
    import sqlite3

    entry = client.enqueue({"event_id": "backoff-event", "payload": {"k": "v"}})
    with client._lock:
        conn = sqlite3.connect(client.db_path)
        try:
            conn.execute(
                "UPDATE outbox SET attempt_count=?, last_attempt_at=?, status='pending' WHERE outbox_id=?",
                (attempt_count, last_attempt_at, entry.outbox_id),
            )
            conn.commit()
        finally:
            conn.close()
    return entry


def test_flush_leaves_a_row_inside_its_backoff_window_untouched(tmp_path, monkeypatch):
    client = _client(tmp_path, retry_base_s=5, retry_max_s=300, max_retries=10)
    calls = []
    monkeypatch.setattr(client, "_deliver", lambda entry: calls.append(entry.outbox_id) or True)
    _stage_failed_row(client, 2, _iso(datetime.now(UTC)))  # window just opened

    assert client.flush(max_attempts=10) == 0
    assert calls == []  # destination was not hit during the backoff window
    stats = client.get_stats()
    assert stats["pending"] == 1 and stats["delivered"] == 0
    assert client.get_pending()[0].attempt_count == 2  # no budget burned


def test_flush_delivers_a_row_once_its_backoff_window_elapsed(tmp_path, monkeypatch):
    client = _client(tmp_path, retry_base_s=5, retry_max_s=300, max_retries=10)
    calls = []
    monkeypatch.setattr(client, "_deliver", lambda entry: calls.append(entry.outbox_id) or True)
    entry = _stage_failed_row(client, 2, "2026-01-01T00:00:00+00:00")  # long elapsed

    assert client.flush(max_attempts=10) == 1
    assert calls == [entry.outbox_id]
    stats = client.get_stats()
    assert stats["delivered"] == 1 and stats["pending"] == 0
