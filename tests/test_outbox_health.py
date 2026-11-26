"""Outbox observability: depth, oldest-pending age, and the readiness rule."""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from assistx import outbox_client as outbox_client_mod
from assistx import runtime as runtime_mod
from assistx.api import app
from assistx.outbox_client import OutboxClient, _bucket_by_attempt, read_queue_health


def _seed(db_path, *, rows):
    client = OutboxClient(db_path=str(db_path), api_url="", auto_flush=False)
    for i in range(rows):
        client.enqueue({"event_id": f"evt-{i}"})
    return client


def _backdate(db_path, hours):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE outbox SET created_at=?", (stamp,))
        conn.commit()
    finally:
        conn.close()


def test_absent_database_is_healthy_and_reports_zero(tmp_path):
    health = read_queue_health(str(tmp_path / "missing.sqlite"))

    assert health["status"] == "absent"
    assert health["ok"] is True
    assert health["pending"] == 0
    assert health["oldest_pending_age_s"] is None


def test_health_reports_depth_and_oldest_pending_age(tmp_path):
    db_path = tmp_path / "outbox.sqlite"
    client = _seed(db_path, rows=3)
    _backdate(db_path, hours=2)

    health = read_queue_health(str(db_path))

    assert health["pending"] == 3
    assert health["dead"] == 0
    assert health["claimed"] == 0
    assert 1.5 * 3600 < health["oldest_pending_age_s"] < 3 * 3600
    assert health["oldest_pending_at"] is not None
    assert health["ok"] is True
    assert health["status"] == "ok"
    # The client view and the probe agree.
    assert client.queue_health()["pending"] == 3


def test_health_flips_unhealthy_past_the_threshold(tmp_path):
    db_path = tmp_path / "outbox.sqlite"
    _seed(db_path, rows=1)
    _backdate(db_path, hours=25)

    health = read_queue_health(str(db_path))

    assert health["oldest_pending_age_s"] > 86400
    assert health["ok"] is False
    assert health["status"] == "stale"
    assert "threshold" in health["reason"]


def test_health_ignores_terminal_rows_when_measuring_age(tmp_path):
    db_path = tmp_path / "outbox.sqlite"
    client = _seed(db_path, rows=1)
    entry = client.enqueue({"event_id": "evt-dead"})
    client._update_status(entry.outbox_id, "dead", 9)
    # Only the terminal row is ancient; the deliverable row is fresh.
    stamp = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE outbox SET created_at=? WHERE outbox_id=?", (stamp, entry.outbox_id)
        )
        conn.commit()
    finally:
        conn.close()

    health = read_queue_health(str(db_path))

    assert health["dead"] == 1
    assert health["pending"] == 1
    assert health["oldest_pending_age_s"] < 3600
    assert health["ok"] is True


def test_probe_does_not_write_to_the_database(tmp_path):
    db_path = tmp_path / "outbox.sqlite"
    _seed(db_path, rows=2)
    before = db_path.read_bytes()

    read_queue_health(str(db_path))

    assert db_path.read_bytes() == before


def _stub_dependencies(monkeypatch):
    monkeypatch.setattr(runtime_mod, "_check_redis", lambda: {"status": "ok"})
    monkeypatch.setattr(runtime_mod, "_check_neo4j", lambda: {"status": "ok"})
    monkeypatch.setattr(runtime_mod, "_check_llm", lambda: {"status": "ok"})
    monkeypatch.setenv("ASSISTX_RUNTIME_PROFILE", "development")


def test_health_payload_exposes_the_outbox_and_fails_readiness_when_stale(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "outbox.sqlite"
    _seed(db_path, rows=4)
    _backdate(db_path, hours=48)
    _stub_dependencies(monkeypatch)
    monkeypatch.setenv("ASSISTX_OUTBOX_DB", str(db_path))

    payload = runtime_mod.build_runtime_health()

    assert payload["outbox"]["pending"] == 4
    assert payload["outbox"]["oldest_pending_age_s"] > 86400
    assert payload["outbox"]["status"] == "stale"
    assert payload["ok"] is False

    response = TestClient(app).get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["outbox"]["pending"] == 4
    assert body["outbox"]["readiness_enforced"] is True


def test_fresh_outbox_keeps_health_green(tmp_path, monkeypatch):
    db_path = tmp_path / "outbox.sqlite"
    _seed(db_path, rows=2)
    _stub_dependencies(monkeypatch)
    monkeypatch.setenv("ASSISTX_OUTBOX_DB", str(db_path))

    payload = runtime_mod.build_runtime_health()

    assert payload["outbox"]["pending"] == 2
    assert payload["outbox"]["ok"] is True
    assert payload["ok"] is True
    assert TestClient(app).get("/health").status_code == 200


def test_readiness_rule_can_be_disabled_while_draining(tmp_path, monkeypatch):
    db_path = tmp_path / "outbox.sqlite"
    _seed(db_path, rows=1)
    _backdate(db_path, hours=72)
    _stub_dependencies(monkeypatch)
    monkeypatch.setenv("ASSISTX_OUTBOX_DB", str(db_path))
    monkeypatch.setenv("ASSISTX_OUTBOX_READINESS", "0")

    payload = runtime_mod.build_runtime_health()

    # Still reported, just not gating readiness.
    assert payload["outbox"]["status"] == "stale"
    assert payload["outbox"]["readiness_enforced"] is False
    assert payload["ok"] is True
    assert TestClient(app).get("/health").status_code == 200


# ---------------------------------------------------------------------------
# DB-derived pending / retry / dead signal (iteration plan R2 §4.2)
#
# These fixtures are in-memory SQLite only. No live outbox DB, WAL, migration
# or network destination is touched, and no in-memory counter is involved:
# every number below is derived from a SELECT over durable rows.
# ---------------------------------------------------------------------------


@pytest.fixture
def attempt_db():
    """In-memory outbox table with the real schema, seeded by each test."""
    conn = sqlite3.connect(":memory:")
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
        yield conn
    finally:
        conn.close()


def _seed_attempt_rows(conn, rows):
    """rows are (outbox_id, status, attempt_count)."""
    stamp = datetime.now(UTC).isoformat()
    conn.executemany(
        "INSERT INTO outbox (outbox_id, event_id, payload_json, attempt_count, "
        "status, created_at) VALUES (?, ?, '{}', ?, ?, ?)",
        [(rid, rid, attempt, status, stamp) for rid, status, attempt in rows],
    )
    conn.commit()


def test_attempt_signal_derives_pending_retry_dead_from_db(attempt_db):
    """A status-only count hides wedged rows; attempt_count exposes them."""
    _seed_attempt_rows(
        attempt_db,
        [
            ("p0-a", "pending", 0),
            ("p0-b", "pending", 0),
            ("p3-a", "pending", 3),
            ("p3-b", "pending", 3),
            ("p3-c", "pending", 3),
            ("p9-a", "pending", 9),
            ("p9-b", "pending", 9),
            ("p9-c", "pending", 9),
            ("p9-d", "pending", 9),
            ("f2", "failed", 2),
            ("dead-1", "dead", 9),
            ("claimed-1", "claimed", 1),
            ("delivered-1", "delivered", 1),
        ],
    )

    rows = attempt_db.execute("SELECT status, attempt_count FROM outbox").fetchall()
    buckets = _bucket_by_attempt(rows, max_retries=10)

    # Nine rows are status='pending', but only two are genuinely untouched.
    assert sum(1 for status, _ in rows if status == "pending") == 9
    assert buckets["pending"] == 2
    assert buckets["retry"] == 8  # 3 at attempt-3 + 4 at attempt-9 + 1 failed
    assert buckets["dead"] == 1
    assert buckets["claimed"] == 1
    assert buckets["delivered"] == 1
    assert buckets["total"] == 13
    assert sum(v for k, v in buckets.items() if k not in ("total", "max_retries")) == 13


def test_attempt_signal_exhausted_budget_is_dead_not_retry(attempt_db):
    """Terminal exhaustion by attempt_count matches the flush() rule."""
    _seed_attempt_rows(
        attempt_db,
        [
            ("edge-9", "pending", 9),   # still inside a 10-attempt budget
            ("edge-10", "pending", 10),  # budget spent -> terminal
            ("failed-10", "failed", 10),
        ],
    )

    rows = attempt_db.execute("SELECT status, attempt_count FROM outbox").fetchall()
    buckets = _bucket_by_attempt(rows, max_retries=10)

    assert buckets["retry"] == 1
    assert buckets["dead"] == 2
    assert buckets["pending"] == 0


def test_read_queue_health_adds_retry_without_breaking_public_shape(
    tmp_path, monkeypatch
):
    """Top-level counts stay status-based; the new signal is additive."""
    monkeypatch.setattr(outbox_client_mod, "MAX_RETRIES", 10)
    db_path = tmp_path / "outbox.sqlite"
    _seed(db_path, rows=2)  # two fresh attempt-0 rows
    client = OutboxClient(db_path=str(db_path), api_url="", auto_flush=False)
    wedged = client.enqueue({"event_id": "evt-wedged"})
    client._update_status(wedged.outbox_id, "pending", 9)  # wedged, not dead
    before = db_path.read_bytes()

    health = read_queue_health(str(db_path))

    # Every pre-existing key and value is unchanged.
    assert health["db_path"] == str(db_path)
    assert health["status"] == "ok"
    assert health["ok"] is True
    assert health["pending"] == 3          # raw status count, as before
    assert health["failed"] == 0
    assert health["claimed"] == 0
    assert health["delivered"] == 0
    assert health["dead"] == 0
    assert health["total"] == 3
    assert health["oldest_pending_age_s"] is not None
    assert "reason" not in health
    # The new attempt-count dimension is additive and DB-derived.
    assert health["retry"] == 1
    assert health["attempt_buckets"]["pending"] == 2
    assert health["attempt_buckets"]["retry"] == 1
    assert health["attempt_buckets"]["dead"] == 0
    assert health["attempt_buckets"]["max_retries"] == 10
    assert db_path.read_bytes() == before  # still a read-only probe


def test_absent_database_reports_empty_attempt_buckets(tmp_path):
    health = read_queue_health(str(tmp_path / "missing.sqlite"))

    assert health["retry"] == 0
    assert health["attempt_buckets"]["total"] == 0
    assert health["attempt_buckets"]["pending"] == 0
    assert health["attempt_buckets"]["retry"] == 0
    assert health["attempt_buckets"]["dead"] == 0


def test_runtime_health_payload_exposes_db_derived_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(outbox_client_mod, "MAX_RETRIES", 10)
    db_path = tmp_path / "outbox.sqlite"
    _seed(db_path, rows=1)
    client = OutboxClient(db_path=str(db_path), api_url="", auto_flush=False)
    wedged = client.enqueue({"event_id": "evt-wedged"})
    client._update_status(wedged.outbox_id, "pending", 9)
    _stub_dependencies(monkeypatch)
    monkeypatch.setenv("ASSISTX_OUTBOX_DB", str(db_path))

    outbox = runtime_mod.build_runtime_health()["outbox"]

    assert outbox["pending"] == 2  # backward-compatible status count
    assert outbox["retry"] == 1
    assert outbox["attempt_buckets"]["pending"] == 1
    assert outbox["attempt_buckets"]["retry"] == 1
    assert outbox["attempt_buckets"]["dead"] == 0
