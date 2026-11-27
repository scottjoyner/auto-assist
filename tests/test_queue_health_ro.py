"""Focused tests for the read-only queue-health CLI.

Fixtures are built in memory and round-tripped to pytest ``tmp_path`` files
because the tool opens its input with ``mode=ro``. No live database is read or
written.
"""

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "queue_health_ro.py"
NOW = "2026-11-27T12:00:00+00:00"
FRESH = "2026-11-27T11:00:00Z"

PRODUCTION_SCHEMA = """
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
);
"""
LEGACY_SCHEMA = """
CREATE TABLE outbox (
  outbox_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  attempt_count INTEGER DEFAULT 0,
  last_attempt_at TEXT,
  status TEXT DEFAULT 'pending',
  created_at TEXT NOT NULL
);
"""

QUARANTINE = "quarantine:stale-attempt9-over-maxattempts5:r16-20261127"
TOKEN = "claim-token-r16-9f8e7d6c-secret-looking"
PAYLOAD_SECRET = "PAYLOAD_BODY_MUST_NEVER_BE_PRINTED"


def _memory_db(schema: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema)
    return conn


def _roundtrip(conn: sqlite3.Connection, path: Path) -> None:
    target = sqlite3.connect(path)
    try:
        conn.backup(target)
        target.commit()
    finally:
        target.close()


def _insert(conn: sqlite3.Connection, index: int, status: str, owner: str | None = None, created: str = FRESH) -> None:
    conn.execute(
        "INSERT INTO outbox VALUES (?,?,?,?,?,?,?,?,?)",
        (f"id-{index}", f"evt-{index}", PAYLOAD_SECRET, 0, None, status, created, owner, None),
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_argv(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _run(path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return _run_argv(str(path), "--now", NOW, *extra)


def _one_json(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    payload = json.loads(lines[0])  # type: ignore[no-any-return]
    assert lines[0] == json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return payload


def test_normal_distribution_with_quarantine_markers_is_read_only(tmp_path):
    conn = _memory_db(PRODUCTION_SCHEMA)
    try:
        for index in range(3):
            _insert(conn, index, "pending")
        for index in range(3, 5):
            _insert(conn, index, "pending", QUARANTINE)
        _insert(conn, 5, "pending", TOKEN)
        _insert(conn, 6, "failed")
        _insert(conn, 7, "claimed")
        _insert(conn, 8, "delivered")
        _insert(conn, 9, "dead")
        conn.commit()
        path = tmp_path / "normal.sqlite"
        _roundtrip(conn, path)
    finally:
        conn.close()

    before = _digest(path)
    before_mtime = path.stat().st_mtime_ns
    result = _run(path)
    after = _digest(path)
    after_mtime = path.stat().st_mtime_ns

    assert result.returncode == 0, result.stderr
    payload = _one_json(result)
    assert payload["db"] == str(path)
    assert payload["query_only"] == 1
    assert payload["readable"] is True
    assert payload["status"] == "ok"
    assert payload["ok"] is True
    assert payload["reason"] is None
    assert payload["total"] == 10
    assert payload["pending"] == 6
    assert payload["failed"] == 1
    assert payload["claimed"] == 1
    assert payload["delivered"] == 1
    assert payload["dead"] == 1
    assert payload["quarantined"] == 2
    assert payload["claimable_pending"] == 4
    assert payload["claimable_pending"] + payload["quarantined"] == payload["pending"]
    assert payload["oldest_pending_at"] == FRESH
    assert payload["oldest_pending_age_s"] == 3600.0
    # Neither payload bodies, opaque owners nor the marker text are printed.
    for secret in (PAYLOAD_SECRET, TOKEN, QUARANTINE):
        assert secret not in result.stdout
        assert secret not in result.stderr
    assert before == after
    assert before_mtime == after_mtime
    assert list(tmp_path.glob("normal.sqlite-*")) == []


def test_legacy_schema_without_claim_owner(tmp_path):
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(LEGACY_SCHEMA)
        for index in range(2):
            conn.execute(
                "INSERT INTO outbox VALUES (?,?,?,?,?,?,?)",
                (f"id-{index}", f"evt-{index}", PAYLOAD_SECRET, 0, None, "pending", FRESH),
            )
        conn.execute(
            "INSERT INTO outbox VALUES (?,?,?,?,?,?,?)",
            ("id-2", "evt-2", PAYLOAD_SECRET, 0, None, "failed", FRESH),
        )
        conn.commit()
        path = tmp_path / "legacy.sqlite"
        _roundtrip(conn, path)
    finally:
        conn.close()

    before = _digest(path)
    result = _run(path)

    assert result.returncode == 0, result.stderr
    payload = _one_json(result)
    assert payload["status"] == "ok"
    assert payload["ok"] is True
    assert payload["reason"] is None
    assert payload["readable"] is True
    assert payload["total"] == 3
    assert payload["pending"] == 2
    assert payload["quarantined"] == 0
    assert payload["claimable_pending"] == 2
    assert payload["oldest_pending_age_s"] == 3600.0
    assert PAYLOAD_SECRET not in result.stdout
    assert before == _digest(path)
    assert list(tmp_path.glob("legacy.sqlite-*")) == []


def test_unreadable_paths_and_missing_table_exit_one(tmp_path):
    missing = tmp_path / "does-not-exist.sqlite"
    result = _run(missing)
    assert result.returncode == 1
    payload = _one_json(result)
    assert payload["readable"] is False
    assert payload["status"] == "absent"
    assert payload["query_only"] is None
    assert payload["reason"] == "outbox database not present"

    directory = tmp_path / "a-directory"
    directory.mkdir()
    result = _run(directory)
    assert result.returncode == 1
    payload = _one_json(result)
    assert payload["readable"] is False
    assert payload["status"] in ("absent", "error")
    assert payload["query_only"] is None
    assert payload["reason"] in ("outbox database not present", "outbox unreadable")

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO other VALUES (1)")
        conn.commit()
        path = tmp_path / "wrong.sqlite"
        _roundtrip(conn, path)
    finally:
        conn.close()
    result = _run(path)
    assert result.returncode == 1
    payload = _one_json(result)
    assert payload["readable"] is False
    assert payload["status"] == "error"
    assert payload["reason"] == "outbox unreadable"
    assert "no such table" not in result.stdout
    assert "no such table" not in result.stderr


def test_negative_control_write_protected_db_is_unchanged(tmp_path):
    conn = _memory_db(PRODUCTION_SCHEMA)
    try:
        _insert(conn, 0, "pending", QUARANTINE)
        conn.commit()
        path = tmp_path / "protected.sqlite"
        _roundtrip(conn, path)
    finally:
        conn.close()
    path.chmod(0o444)
    before = _digest(path)
    before_size = path.stat().st_size

    result = _run(path)

    assert result.returncode == 0, result.stderr
    assert _one_json(result)["quarantined"] == 1
    assert _digest(path) == before
    assert path.stat().st_size == before_size
    assert list(tmp_path.glob("protected.sqlite-*")) == []


def test_stale_queue_is_reported_but_still_exits_zero(tmp_path):
    conn = _memory_db(PRODUCTION_SCHEMA)
    try:
        _insert(conn, 0, "pending", created="2026-11-25T00:00:00Z")
        conn.commit()
        path = tmp_path / "stale.sqlite"
        _roundtrip(conn, path)
    finally:
        conn.close()

    before = _digest(path)
    result = _run(path)

    assert result.returncode == 0, result.stderr
    payload = _one_json(result)
    assert payload["status"] == "stale"
    assert payload["ok"] is False
    assert payload["readable"] is True
    assert payload["reason"].startswith("oldest pending event is")
    assert before == _digest(path)


def test_help_is_available():
    result = _run_argv("--help")
    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert "db_path" in result.stdout
    assert "--now" in result.stdout


def test_health_fields_come_from_imported_read_queue_health(tmp_path):
    conn = _memory_db(PRODUCTION_SCHEMA)
    try:
        _insert(conn, 0, "pending", QUARANTINE)
        _insert(conn, 1, "failed")
        conn.commit()
        path = tmp_path / "reuse.sqlite"
        _roundtrip(conn, path)
    finally:
        conn.close()

    from assistx.outbox_client import read_queue_health

    spec = importlib.util.spec_from_file_location("queue_health_ro", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    reference = datetime.fromisoformat(NOW)
    expected = read_queue_health(str(path), now=reference)
    payload, code = module.inspect(str(path), now=reference)

    assert code == 0
    for key, value in expected.items():
        if key != "db_path":
            assert payload[key] == value
