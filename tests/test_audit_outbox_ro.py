"""Focused tests for the read-only outbox audit tool.

Fixtures are built in an in-memory SQLite database and round-tripped to a
pytest ``tmp_path`` file, because the tool deliberately opens its input with
``mode=ro``. No live database is read or written.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "audit_outbox_ro.py"

JSON_KEYS = {
    "db",
    "size_bytes",
    "mtime",
    "detected_columns",
    "total_rows",
    "status_distribution",
    "attempt_distribution",
    "claim_owner_counts",
    "pass",
}
EMPTY_COLUMNS = {"status": [], "attempt_count": [], "claim_owner": [], "timestamps": []}

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

QUARANTINE = "quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126"
TOKEN = "claim-token-9f8e7d6c-secret-looking"
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


def _run(path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, str(TOOL), str(path), *extra],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_normal_distribution_is_reported_and_database_is_unchanged(tmp_path):
    conn = _memory_db(PRODUCTION_SCHEMA)
    try:
        rows = [(attempt, QUARANTINE if attempt == 9 else None) for attempt in (0,) * 28 + (3,) * 6 + (9,) * 14]
        for index, (attempt, owner) in enumerate(rows):
            conn.execute(
                "INSERT INTO outbox VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"id-{index:03d}",
                    f"evt-{index:03d}",
                    PAYLOAD_SECRET,
                    attempt,
                    "2026-11-19T04:16:00Z" if attempt == 9 else None,
                    "pending",
                    "2026-11-14T14:41:19Z",
                    owner,
                    None,
                ),
            )
        conn.commit()
        path = tmp_path / "outbox.sqlite"
        _roundtrip(conn, path)
    finally:
        conn.close()

    before = _digest(path)
    result = _run(path)
    after = _digest(path)

    assert result.returncode == 0, result.stderr
    assert "Total rows: 48" in result.stdout
    assert "status: status" in result.stdout
    assert "attempt_count: attempt_count" in result.stdout
    assert "claim_owner: claim_owner" in result.stdout
    assert "pending: 48" in result.stdout
    assert "0: 28" in result.stdout
    assert "3: 6" in result.stdout
    assert "9: 14" in result.stdout
    assert f"{QUARANTINE}: 14" in result.stdout
    assert "Quarantine markers total: 14" in result.stdout
    assert "payload" not in result.stdout.lower()
    assert PAYLOAD_SECRET not in result.stdout
    assert PAYLOAD_SECRET not in result.stderr
    assert before == after
    assert list(tmp_path.glob("outbox.sqlite-*")) == []


def test_quarantine_markers_are_printed_but_opaque_owners_are_redacted(tmp_path):
    conn = _memory_db(PRODUCTION_SCHEMA)
    try:
        for index, owner in enumerate([QUARANTINE, QUARANTINE, TOKEN, None]):
            conn.execute(
                "INSERT INTO outbox VALUES (?,?,?,?,?,?,?,?,?)",
                (f"id-{index}", f"evt-{index}", "{}", 9, None, "pending", "2026-11-26T00:00:00Z", owner, None),
            )
        conn.commit()
        path = tmp_path / "owners.sqlite"
        _roundtrip(conn, path)
    finally:
        conn.close()

    result = _run(path)

    assert result.returncode == 0, result.stderr
    assert f"{QUARANTINE}: 2" in result.stdout
    assert "<redacted>: 1" in result.stdout
    assert "NULL: 1" in result.stdout
    assert "Quarantine markers total: 2" in result.stdout
    assert TOKEN not in result.stdout
    assert TOKEN not in result.stderr


def test_missing_expected_table_exits_one(tmp_path):
    conn = _memory_db("CREATE TABLE other (id INTEGER PRIMARY KEY);")
    try:
        conn.execute("INSERT INTO other VALUES (1)")
        conn.commit()
        path = tmp_path / "wrong.sqlite"
        _roundtrip(conn, path)
    finally:
        conn.close()

    result = _run(path)

    assert result.returncode == 1
    assert "expected table 'outbox' is absent" in result.stderr
    assert "Total rows" not in result.stdout


def test_unreadable_path_exits_one(tmp_path):
    missing = tmp_path / "does-not-exist.sqlite"
    result = _run(missing)
    assert result.returncode == 1
    assert "cannot audit database" in result.stderr

    directory = tmp_path / "a-directory"
    directory.mkdir()
    result = _run(directory)
    assert result.returncode == 1
    assert "cannot audit database" in result.stderr


def test_schema_aware_when_optional_columns_are_absent(tmp_path):
    conn = _memory_db("CREATE TABLE outbox (id INTEGER PRIMARY KEY, note TEXT);")
    try:
        conn.execute("INSERT INTO outbox VALUES (1, 'legacy')")
        conn.execute("INSERT INTO outbox VALUES (2, 'legacy')")
        conn.commit()
        path = tmp_path / "legacy.sqlite"
        _roundtrip(conn, path)
    finally:
        conn.close()

    result = _run(path)

    assert result.returncode == 0, result.stderr
    assert "Total rows: 2" in result.stdout
    assert "status: absent" in result.stdout
    assert "attempt_count: absent" in result.stdout
    assert "claim_owner: absent" in result.stdout
    assert "timestamps: absent" in result.stdout
    assert "Status distribution: unavailable (column absent)" in result.stdout
    assert "Attempt distribution: unavailable (column absent)" in result.stdout
    assert "Claim-owner counts: unavailable (column absent)" in result.stdout


def _load_json(result: subprocess.CompletedProcess[str]) -> dict:
    """JSON mode must emit exactly one compact, key-sorted object on stdout."""
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    data = json.loads(lines[0])
    assert set(data) == JSON_KEYS
    assert result.stdout == json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n"
    return data


def _normal_production_db(tmp_path: Path) -> Path:
    conn = _memory_db(PRODUCTION_SCHEMA)
    try:
        rows = [(attempt, QUARANTINE if attempt == 9 else None) for attempt in (0,) * 28 + (3,) * 6 + (9,) * 14]
        for index, (attempt, owner) in enumerate(rows):
            conn.execute(
                "INSERT INTO outbox VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"id-{index:03d}",
                    f"evt-{index:03d}",
                    PAYLOAD_SECRET,
                    attempt,
                    "2026-11-19T04:16:00Z" if attempt == 9 else None,
                    "pending",
                    "2026-11-14T14:41:19Z",
                    owner,
                    None,
                ),
            )
        conn.commit()
        path = tmp_path / "json-normal.sqlite"
        _roundtrip(conn, path)
    finally:
        conn.close()
    return path


def test_json_normal_distribution_shape_and_redaction(tmp_path):
    path = _normal_production_db(tmp_path)
    before = _digest(path)

    result = _run(path, "--json")

    after = _digest(path)
    assert result.returncode == 0, result.stderr
    data = _load_json(result)

    assert data["pass"] is True
    assert data["db"] == str(path.resolve())
    assert data["size_bytes"] == path.stat().st_size
    assert isinstance(data["mtime"], str) and data["mtime"]
    assert data["detected_columns"] == {
        "status": ["status"],
        "attempt_count": ["attempt_count"],
        "claim_owner": ["claim_owner"],
        "timestamps": ["claimed_at", "created_at", "last_attempt_at"],
    }
    assert data["total_rows"] == 48
    assert data["status_distribution"] == {"pending": 48}
    assert data["attempt_distribution"] == {"0": 28, "3": 6, "9": 14}
    assert data["claim_owner_counts"] == {"NULL": 34, QUARANTINE: 14}
    assert PAYLOAD_SECRET not in result.stdout
    assert PAYLOAD_SECRET not in result.stderr
    assert "payload" not in result.stdout.lower()
    assert before == after
    assert list(tmp_path.glob("json-normal.sqlite-*")) == []


def test_json_quarantine_markers_printed_opaque_owners_redacted(tmp_path):
    conn = _memory_db(PRODUCTION_SCHEMA)
    try:
        for index, owner in enumerate([QUARANTINE, QUARANTINE, TOKEN, None]):
            conn.execute(
                "INSERT INTO outbox VALUES (?,?,?,?,?,?,?,?,?)",
                (f"id-{index}", f"evt-{index}", "{}", 9, None, "pending", "2026-11-26T00:00:00Z", owner, None),
            )
        conn.commit()
        path = tmp_path / "json-owners.sqlite"
        _roundtrip(conn, path)
    finally:
        conn.close()

    result = _run(path, "--json")

    assert result.returncode == 0, result.stderr
    data = _load_json(result)
    assert data["pass"] is True
    assert data["claim_owner_counts"] == {QUARANTINE: 2, "<redacted>": 1, "NULL": 1}
    assert TOKEN not in result.stdout
    assert TOKEN not in result.stderr


def test_json_missing_table_sets_pass_false(tmp_path):
    conn = _memory_db("CREATE TABLE other (id INTEGER PRIMARY KEY);")
    try:
        conn.execute("INSERT INTO other VALUES (1)")
        conn.commit()
        path = tmp_path / "json-wrong.sqlite"
        _roundtrip(conn, path)
    finally:
        conn.close()

    result = _run(path, "--json")

    assert result.returncode == 1
    data = _load_json(result)
    assert data["pass"] is False
    assert data["db"] == str(path.resolve())
    assert data["detected_columns"] == EMPTY_COLUMNS
    assert data["total_rows"] is None
    assert data["size_bytes"] is None
    assert data["mtime"] is None
    assert data["status_distribution"] == {}
    assert data["attempt_distribution"] == {}
    assert data["claim_owner_counts"] == {}


def test_json_unreadable_path_sets_pass_false(tmp_path):
    missing = tmp_path / "json-does-not-exist.sqlite"
    result = _run(missing, "--json")
    assert result.returncode == 1
    data = _load_json(result)
    assert data["pass"] is False
    assert data["db"] == str(missing.resolve())
    assert data["detected_columns"] == EMPTY_COLUMNS
    assert data["total_rows"] is None

    directory = tmp_path / "json-a-directory"
    directory.mkdir()
    result = _run(directory, "--json")
    assert result.returncode == 1
    data = _load_json(result)
    assert data["pass"] is False
    assert data["db"] == str(directory.resolve())
    assert data["detected_columns"] == EMPTY_COLUMNS
