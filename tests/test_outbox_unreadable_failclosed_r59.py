"""Round 59: an unreadable outbox queue must not be reported as verified.

``read_queue_health()`` opened the live SQLite queue read-only and swallowed
``sqlite3.Error`` with ``ok: True``.  Round 39 hardened only the *exception*
path in ``runtime._check_outbox()`` (when the probe itself raises), but the
common corruption/lock/permission failure is caught *inside* the probe and
returned as a green verdict, so readiness stayed healthy with zero queue
evidence.  A missing database still means "no queue yet" and stays healthy.

Hermetic: dict fixtures and files under pytest's tmp_path only.  No live
outbox DB, SQLite WAL, socket, service, credential or subprocess.
"""

from __future__ import annotations

import pytest

from assistx import runtime as runtime_mod
from assistx.outbox_client import OutboxClient, read_queue_health


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSISTX_RUNTIME_PROFILE", "development")
    monkeypatch.delenv("ASSISTX_OUTBOX_READINESS", raising=False)
    monkeypatch.setattr(runtime_mod, "_check_redis", lambda: {"status": "ok"})
    monkeypatch.setattr(runtime_mod, "_check_neo4j", lambda: {"status": "ok"})
    monkeypatch.setattr(runtime_mod, "_check_llm", lambda: {"status": "ok"})
    return tmp_path


def _corrupt_db(path):
    path.write_bytes(b"not a sqlite database\n" * 4)
    return path


def test_unreadable_queue_is_not_ok(isolated):
    db = _corrupt_db(isolated / "corrupt.sqlite")
    before = db.read_bytes()

    health = read_queue_health(str(db))

    assert health["status"] == "error"
    assert health["ok"] is False
    assert "outbox unreadable" in health["reason"]
    # The probe is still strictly read-only.
    assert db.read_bytes() == before


def test_unreadable_queue_fails_runtime_readiness(isolated, monkeypatch):
    db = _corrupt_db(isolated / "corrupt.sqlite")
    monkeypatch.setenv("ASSISTX_OUTBOX_DB", str(db))

    payload = runtime_mod.build_runtime_health()

    assert payload["outbox"]["ok"] is False
    assert payload["outbox"]["readiness_enforced"] is True
    assert payload["ok"] is False
    assert payload["status"] == "degraded"


def test_drain_override_keeps_payload_honest(isolated, monkeypatch):
    db = _corrupt_db(isolated / "corrupt.sqlite")
    monkeypatch.setenv("ASSISTX_OUTBOX_DB", str(db))
    monkeypatch.setenv("ASSISTX_OUTBOX_READINESS", "0")

    payload = runtime_mod.build_runtime_health()

    # The operator override may keep readiness green, but it must not turn an
    # unreadable queue into a verified one.
    assert payload["outbox"]["status"] == "error"
    assert payload["outbox"]["ok"] is False
    assert payload["outbox"]["readiness_enforced"] is False
    assert payload["ok"] is True


def test_missing_database_remains_healthy(isolated):
    health = read_queue_health(str(isolated / "missing.sqlite"))

    assert health["status"] == "absent"
    assert health["ok"] is True
    assert health["pending"] == 0


def test_fresh_queue_remains_healthy(isolated):
    db = isolated / "fresh.sqlite"
    client = OutboxClient(db_path=str(db), api_url="", auto_flush=False)
    client.enqueue({"event_id": "evt-r59"})

    health = read_queue_health(str(db))

    assert health["status"] == "ok"
    assert health["ok"] is True
    assert health["pending"] == 1
