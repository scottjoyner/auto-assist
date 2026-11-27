"""Round-7 guard: additive outbox-health keys must not break the /health consumer.

Producer (NOT on this branch): `agent/outbox-health-db-derived-r3-20261126`
@ `b43d353` adds top-level `retry` and nested `attempt_buckets` to
`assistx.outbox_client.read_queue_health()`; the legacy status keys keep their
old meaning. This branch is based on `origin/main` `a300072`, which does not
carry that producer.

Consumers identified from local code:

* On this branch (origin/main): ``assistx.api.health()`` builds a JSONResponse
  from ``build_runtime_health()`` and only reads ``payload.get("ok")`` for the
  status code; there is no strict response schema.
* On the pushed PR-ready outbox branch (local worktree
  ``wt-assistx-outbox-pr-r6-20261126``): ``assistx.runtime._check_outbox()``
  calls the producer, adds ``readiness_enforced``, and the resulting dict
  becomes ``build_runtime_health()["outbox"]``; readiness reads only
  ``.get("ok")`` and ``.get("readiness_enforced")``, then the same ``/health``
  response passes the dict through unchanged.

This test derives a legacy-shaped payload from an in-memory SQLite fixture,
adds the two keys as the producer does, and feeds it through the real
``api.health()`` consumer. No live DB, WAL, network, file write or service call.
"""

import json
import sqlite3

from assistx import api as api_mod

LEGACY_KEYS = (
    "db_path",
    "status",
    "ok",
    "stale_after_s",
    "pending",
    "failed",
    "claimed",
    "delivered",
    "dead",
    "total",
    "oldest_pending_at",
    "oldest_pending_age_s",
)


def _health_with_additive_keys():
    """Legacy /health payload plus the R3 additive keys, from :memory: rows."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE outbox ("
            "outbox_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "attempt_count INTEGER NOT NULL DEFAULT 0, created_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO outbox (outbox_id, status, attempt_count, created_at) VALUES (?, ?, ?, ?)",
            [
                ("p0", "pending", 0, "2026-11-26T00:00:00+00:00"),
                ("r3", "pending", 3, "2026-11-26T00:00:00+00:00"),
                ("r9", "pending", 9, "2026-11-26T00:00:00+00:00"),
                ("dl", "delivered", 0, "2026-11-26T00:00:00+00:00"),
            ],
        )
        rows = conn.execute("SELECT status, COUNT(*) FROM outbox GROUP BY status").fetchall()
    finally:
        conn.close()

    outbox = {
        "db_path": ":memory:",
        "status": "ok",
        "ok": True,
        "reason": None,
        "stale_after_s": 86400,
        "pending": 0,
        "failed": 0,
        "claimed": 0,
        "delivered": 0,
        "dead": 0,
        "total": 0,
        "oldest_pending_at": None,
        "oldest_pending_age_s": None,
    }
    for status, count in rows:
        outbox[status] = int(count)
        outbox["total"] += int(count)

    # Exact additive shape introduced by b43d353 (mirrored, not imported:
    # that producer is on a different branch and must not be modified here).
    outbox["retry"] = 1
    outbox["attempt_buckets"] = {
        "total": 4,
        "pending": 1,
        "retry": 1,
        "dead": 1,
        "claimed": 0,
        "delivered": 1,
        "failed": 0,
        "max_retries": 10,
    }
    return {"ok": True, "status": "ok", "outbox": outbox}


def test_health_consumer_keeps_legacy_shape_with_additive_keys(monkeypatch):
    payload = _health_with_additive_keys()
    monkeypatch.setattr(api_mod, "build_runtime_health", lambda: payload)

    response = api_mod.health()

    assert response.status_code == 200
    body = json.loads(response.body)
    outbox = body["outbox"]
    for key in LEGACY_KEYS:
        assert key in outbox
        assert outbox[key] == payload["outbox"][key]
    assert outbox["pending"] == 3  # legacy status count, unchanged
    assert outbox["retry"] == 1
    assert outbox["attempt_buckets"]["max_retries"] == 10
    assert body["ok"] is True


def test_health_consumer_503_still_driven_by_legacy_ok(monkeypatch):
    payload = _health_with_additive_keys()
    payload["ok"] = False
    payload["status"] = "degraded"
    payload["outbox"]["ok"] = False
    monkeypatch.setattr(api_mod, "build_runtime_health", lambda: payload)

    response = api_mod.health()

    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["outbox"]["retry"] == 1
    assert body["outbox"]["attempt_buckets"]["dead"] == 1
