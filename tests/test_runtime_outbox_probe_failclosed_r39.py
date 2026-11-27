"""Outbox readiness must fail closed when the health probe itself fails (r39).

The outbox probe is a read-only SQLite status read. If that read raises
(corrupt file, permission/disk error, unexpected schema), readiness must not
silently collapse to green: an unverifiable queue is not a verified queue.

All checks are hermetic: the probe is stubbed in-process, and the one real
probe uses a path under pytest's tmp_path. No live DB/WAL, service or network.
"""

import pytest
from fastapi.testclient import TestClient

from assistx import outbox_client
from assistx import runtime as runtime_mod
from assistx.api import app


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSISTX_OUTBOX_DB", str(tmp_path / "outbox.sqlite"))
    monkeypatch.delenv("ASSISTX_OUTBOX_READINESS", raising=False)
    monkeypatch.setenv("ASSISTX_RUNTIME_PROFILE", "development")
    monkeypatch.setattr(runtime_mod, "_check_redis", lambda: {"status": "ok"})
    monkeypatch.setattr(runtime_mod, "_check_neo4j", lambda: {"status": "ok"})
    monkeypatch.setattr(runtime_mod, "_check_llm", lambda: {"status": "ok"})
    return tmp_path


def _boom(*args, **kwargs):
    raise OSError("simulated outbox read failure")


def test_probe_failure_is_not_ok(isolated, monkeypatch):
    monkeypatch.setattr(outbox_client, "read_queue_health", _boom)

    health = runtime_mod._check_outbox()

    assert health["status"] == "error"
    assert health["ok"] is False
    assert "outbox probe failed" in health["reason"]


def test_probe_failure_fails_runtime_readiness(isolated, monkeypatch):
    monkeypatch.setattr(outbox_client, "read_queue_health", _boom)

    payload = runtime_mod.build_runtime_health()

    assert payload["outbox"]["ok"] is False
    assert payload["ok"] is False
    assert payload["status"] == "degraded"


def test_probe_failure_yields_503(isolated, monkeypatch):
    monkeypatch.setattr(outbox_client, "read_queue_health", _boom)

    assert TestClient(app).get("/health").status_code == 503


def test_drain_override_still_keeps_health_green(isolated, monkeypatch):
    monkeypatch.setattr(outbox_client, "read_queue_health", _boom)
    monkeypatch.setenv("ASSISTX_OUTBOX_READINESS", "0")

    payload = runtime_mod.build_runtime_health()

    assert payload["outbox"]["readiness_enforced"] is False
    assert payload["ok"] is True


def test_real_absent_db_is_healthy(isolated):
    health = runtime_mod._check_outbox()

    assert health["status"] == "absent"
    assert health["ok"] is True


def test_healthy_probe_still_green(isolated, monkeypatch):
    monkeypatch.setattr(
        outbox_client,
        "read_queue_health",
        lambda path: {"status": "ok", "ok": True, "pending": 0},
    )

    payload = runtime_mod.build_runtime_health()

    assert payload["outbox"]["ok"] is True
    assert payload["ok"] is True
    assert TestClient(app).get("/health").status_code == 200
