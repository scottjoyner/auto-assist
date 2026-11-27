"""Offline integration test tying the two AssistX read-only CLIs together.

``tools/audit_outbox_ro.py --json`` and ``tools/queue_health_ro.py`` are run as
separate processes against the *same* temporary SQLite fixture (built in memory
and round-tripped to a pytest ``tmp_path`` file). The two JSON payloads are
parsed and checked for cross-CLI consistency, read-only evidence, redaction and
file immutability. No live database is opened and neither CLI is imported in
process; only subprocess invocations are used.

Scratch containment: pytest's cache provider is disabled by the caller, bytecode
writes are suppressed, and TMPDIR/PYTHONPYCACHEPREFIX point at the SSD scratch
directory.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "tools" / "audit_outbox_ro.py"
HEALTH = ROOT / "tools" / "queue_health_ro.py"
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

QUARANTINE = "quarantine:stale-attempt9-over-maxattempts5:r17-20261127"
TOKEN = "claim-token-r17-9f8e7d6c-secret-looking"
PAYLOAD_SECRET = "PAYLOAD_BODY_MUST_NEVER_BE_PRINTED"
CREDENTIAL_SHAPED = "ASSISTX_AUTH_PASS=hunter2-r17-never-print-me"
PAYLOAD = f'{{"note":"{PAYLOAD_SECRET}","auth":"{CREDENTIAL_SHAPED}"}}'

# (status, attempt_count, claim_owner) -- all rows are one hour old, inside the
# 24 h staleness threshold, so both CLIs report a healthy queue.
NORMAL_ROWS = [
    ("pending", 0, None),
    ("pending", 0, None),
    ("pending", 0, None),
    ("pending", 3, QUARANTINE),
    ("pending", 3, QUARANTINE),
    ("pending", 0, TOKEN),
    ("failed", 1, None),
    ("claimed", 2, None),
    ("delivered", 0, None),
    ("dead", 5, None),
]
EXPECTED_STATUS = {"pending": 6, "failed": 1, "claimed": 1, "delivered": 1, "dead": 1}
EXPECTED_ATTEMPTS = {"0": 5, "1": 1, "2": 1, "3": 2, "5": 1}
EXPECTED_OWNERS = {"NULL": 7, QUARANTINE: 2, "<redacted>": 1}

# Legacy fixture: no claim_owner column, two pending and one failed row.
LEGACY_ROWS = [("pending", 0), ("pending", 1), ("failed", 2)]
EXPECTED_LEGACY_STATUS = {"pending": 2, "failed": 1}
EXPECTED_LEGACY_ATTEMPTS = {"0": 1, "1": 1, "2": 1}

SECRETS = (PAYLOAD_SECRET, CREDENTIAL_SHAPED, TOKEN)


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _one_json(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """Each CLI must emit exactly one compact, key-sorted JSON object."""
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    payload = json.loads(lines[0])
    assert lines[0] == json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return payload


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture(path: Path, schema: str, rows: list[tuple]) -> None:
    """Build the fixture in memory, then round-trip it onto the SSD tmp_path."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(schema)
        for index, row in enumerate(rows):
            if len(row) == 3:
                status, attempt, owner = row
                conn.execute(
                    "INSERT INTO outbox VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"id-{index}", f"evt-{index}", PAYLOAD, attempt, None, status, FRESH, owner, None),
                )
            else:
                status, attempt = row
                conn.execute(
                    "INSERT INTO outbox VALUES (?,?,?,?,?,?,?)",
                    (f"id-{index}", f"evt-{index}", PAYLOAD, attempt, None, status, FRESH),
                )
        conn.commit()
        target = sqlite3.connect(path)
        try:
            conn.backup(target)
            target.commit()
        finally:
            target.close()
    finally:
        conn.close()


def _assert_no_secrets(*results: subprocess.CompletedProcess[str]) -> None:
    for result in results:
        for secret in SECRETS:
            assert secret not in result.stdout
            assert secret not in result.stderr


def _quarantine_total(audit: dict[str, object]) -> int:
    counts = audit["claim_owner_counts"]
    assert isinstance(counts, dict)
    return sum(count for label, count in counts.items() if str(label).lower().startswith("quarantine:"))


def test_both_clis_agree_on_normal_distribution_and_are_read_only(tmp_path: Path) -> None:
    path = tmp_path / "normal.sqlite"
    _write_fixture(path, PRODUCTION_SCHEMA, NORMAL_ROWS)

    before = _digest(path)
    before_mtime = path.stat().st_mtime_ns
    audit_result = _run(AUDIT, str(path), "--json")
    after_audit = _digest(path)
    health_result = _run(HEALTH, str(path), "--now", NOW)
    after_health = _digest(path)
    # The human audit path is the only one that states the open mode explicitly.
    human_audit = _run(AUDIT, str(path))

    assert audit_result.returncode == 0, audit_result.stderr
    assert health_result.returncode == 0, health_result.stderr
    assert human_audit.returncode == 0, human_audit.stderr

    audit = _one_json(audit_result)
    health = _one_json(health_result)

    # Same fixture, same reported database, same key-sorted single-object contract.
    resolved = str(path.resolve())
    assert audit["db"] == health["db"] == resolved
    assert set(audit) == {
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
    assert audit["size_bytes"] == path.stat().st_size

    # Total agrees across both CLIs and across all audit distributions.
    assert audit["total_rows"] == 10
    assert health["total"] == 10
    assert sum(audit["status_distribution"].values()) == audit["total_rows"]
    assert sum(audit["attempt_distribution"].values()) == audit["total_rows"]
    assert sum(audit["claim_owner_counts"].values()) == audit["total_rows"]

    # Pending and the other status buckets agree; the audit's attempt buckets match
    # the fixture ground truth (the health CLI deliberately reports no attempts).
    assert audit["status_distribution"] == EXPECTED_STATUS
    assert audit["attempt_distribution"] == EXPECTED_ATTEMPTS
    assert audit["claim_owner_counts"] == EXPECTED_OWNERS
    assert health["pending"] == audit["status_distribution"]["pending"] == 6
    for status in ("failed", "claimed", "delivered", "dead"):
        assert health[status] == audit["status_distribution"][status]

    # Quarantine markers are printable audit labels but invisible in health output;
    # both counts must still agree, and claimable_pending must reconcile with the
    # audit owner totals after removing quarantined and non-pending rows.
    q_from_audit = _quarantine_total(audit)
    assert q_from_audit == health["quarantined"] == 2
    non_pending = health["total"] - health["pending"]
    claimable_from_audit = sum(audit["claim_owner_counts"].values()) - q_from_audit - non_pending
    assert claimable_from_audit == health["claimable_pending"] == 4
    assert health["claimable_pending"] + health["quarantined"] == health["pending"]
    assert QUARANTINE not in health_result.stdout
    assert QUARANTINE not in health_result.stderr

    # Read-only evidence: query_only is present on the health payload; the audit
    # declares mode=ro/query_only on its human path. Neither claims a write verdict.
    assert health["query_only"] == 1
    assert health["readable"] is True
    assert health["status"] == "ok"
    assert health["ok"] is True
    assert health["reason"] is None
    assert audit["pass"] is True
    assert "Open mode: mode=ro, query_only=1 (ON=1)" in human_audit.stdout

    # No payload bodies, embedded credentials, opaque claim tokens or sqlite text.
    _assert_no_secrets(audit_result, health_result, human_audit)
    assert "payload" not in audit_result.stdout.lower()
    assert "payload" not in health_result.stdout.lower()
    assert "ASSISTX_AUTH_PASS" not in audit_result.stdout + audit_result.stderr
    assert "ASSISTX_AUTH_PASS" not in health_result.stdout + health_result.stderr

    # Neither CLI touched the fixture file or left journal/WAL sidecars.
    assert before == after_audit == after_health == _digest(path)
    assert path.stat().st_mtime_ns == before_mtime
    assert list(tmp_path.glob("normal.sqlite-*")) == []


def test_both_clis_agree_on_legacy_schema_without_claim_owner(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    _write_fixture(path, LEGACY_SCHEMA, LEGACY_ROWS)

    before = _digest(path)
    audit_result = _run(AUDIT, str(path), "--json")
    health_result = _run(HEALTH, str(path), "--now", NOW)

    assert audit_result.returncode == 0, audit_result.stderr
    assert health_result.returncode == 0, health_result.stderr
    audit = _one_json(audit_result)
    health = _one_json(health_result)

    assert audit["db"] == health["db"] == str(path.resolve())
    assert audit["detected_columns"]["claim_owner"] == []
    assert audit["detected_columns"]["status"] == ["status"]
    assert audit["total_rows"] == health["total"] == 3
    assert sum(audit["status_distribution"].values()) == audit["total_rows"]
    assert sum(audit["attempt_distribution"].values()) == audit["total_rows"]
    assert audit["status_distribution"] == EXPECTED_LEGACY_STATUS
    assert audit["attempt_distribution"] == EXPECTED_LEGACY_ATTEMPTS
    assert audit["claim_owner_counts"] == {}
    assert health["pending"] == audit["status_distribution"]["pending"] == 2
    assert health["failed"] == audit["status_distribution"]["failed"] == 1
    assert _quarantine_total(audit) == health["quarantined"] == 0
    assert health["claimable_pending"] == health["pending"] == 2
    assert health["query_only"] == 1
    assert health["readable"] is True
    assert health["status"] == "ok"
    assert health["reason"] is None
    assert audit["pass"] is True
    _assert_no_secrets(audit_result, health_result)
    assert before == _digest(path)
    assert list(tmp_path.glob("legacy.sqlite-*")) == []


def test_both_clis_fail_closed_on_unreadable_paths(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite"
    audit_result = _run(AUDIT, str(missing), "--json")
    health_result = _run(HEALTH, str(missing))
    assert audit_result.returncode == 1
    assert health_result.returncode == 1
    audit = _one_json(audit_result)
    health = _one_json(health_result)
    assert audit["db"] == health["db"] == str(missing.resolve())
    assert audit["pass"] is False
    assert audit["total_rows"] is None
    assert health["readable"] is False
    assert health["status"] == "absent"
    assert health["query_only"] is None
    assert health["reason"] == "outbox database not present"

    directory = tmp_path / "a-directory"
    directory.mkdir()
    audit_result = _run(AUDIT, str(directory), "--json")
    health_result = _run(HEALTH, str(directory))
    assert audit_result.returncode == 1
    assert health_result.returncode == 1
    assert _one_json(audit_result)["pass"] is False
    health = _one_json(health_result)
    assert health["readable"] is False
    assert health["status"] in ("absent", "error")
    assert health["reason"] in ("outbox database not present", "outbox unreadable")

    # A readable database without an outbox table is a gate, not a default-success.
    wrong = tmp_path / "wrong.sqlite"
    _write_fixture(wrong, "CREATE TABLE other (id INTEGER PRIMARY KEY);", [])
    before = _digest(wrong)
    audit_result = _run(AUDIT, str(wrong), "--json")
    health_result = _run(HEALTH, str(wrong))
    assert audit_result.returncode == 1
    assert health_result.returncode == 1
    audit = _one_json(audit_result)
    health = _one_json(health_result)
    assert audit["db"] == health["db"] == str(wrong.resolve())
    assert audit["pass"] is False
    assert audit["total_rows"] is None
    assert health["readable"] is False
    assert health["query_only"] == 1  # the ro connection opened; the table gate failed
    assert health["reason"] == "outbox unreadable"
    assert "no such table" not in audit_result.stdout + audit_result.stderr
    assert "no such table" not in health_result.stdout + health_result.stderr
    _assert_no_secrets(audit_result, health_result)
    assert before == _digest(wrong)
    assert list(tmp_path.glob("wrong.sqlite-*")) == []
