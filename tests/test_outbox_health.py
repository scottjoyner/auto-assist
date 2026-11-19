"""Outbox observability: depth, oldest-pending age, and the readiness rule."""

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from assistx import runtime as runtime_mod
from assistx.api import app
from assistx.outbox_client import OutboxClient, read_queue_health


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
