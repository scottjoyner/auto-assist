"""Tie the R4 quarantine marker contract to the R11 claim guard (r18).

The hold label written by the R4 reconciliation is a single exact string,
recorded verbatim in ``round4-session1-outbox-20261126.md`` and re-observed by
the read-only audit in ``round15-session4-assistx-20261126.md``. Three consumers
must agree on that one string:

1. the R11 claim guard SQL (``LIKE 'quarantine:%'``),
2. the read-only health ``quarantined``/``claimable_pending`` counts, and
3. the read-only audit's printable ``claim_owner`` label.

Every database here is either a named in-memory SQLite database or a snapshot
of that database round-tripped to pytest ``tmp_path`` on the SSD scratch disk.
No live database is opened, and no production code is imported for mutation.
"""

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

import assistx.outbox_client as oc
from assistx.outbox_client import OutboxClient

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "tools" / "audit_outbox_ro.py"
HEALTH = ROOT / "tools" / "queue_health_ro.py"
R4_REPORT = Path("/home/scott/opencode-reviews/round4-session1-outbox-20261126.md")

# Verbatim R4 reconciliation hold label (round4-session1 report, SQL UPDATE at
# line 114; re-confirmed in the round15-session4 read-only audit JSON).
R4_MARKER = "quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126"
OPAQUE_OWNER = "claim-token-3f9c1d8e7b6a5f40-never-print-me"
PAYLOAD = '{"note":"PAYLOAD_SENTINEL_NEVER_PRINTED"}'
NOW = "2026-11-27T12:00:00+00:00"
FRESH = "2026-11-27T11:00:00+00:00"
MEM_PATH = ":memory:"
SCHEMA = """
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


def _audit_module():
    """Load the read-only audit tool as a module (no CLI execution)."""
    spec = importlib.util.spec_from_file_location("assistx_audit_ro_r18", AUDIT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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
    lines = result.stdout.splitlines()
    assert len(lines) == 1, result.stdout
    payload = json.loads(lines[0])
    assert lines[0] == json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return payload


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def mem_outbox(monkeypatch):
    """One shared in-memory SQLite DB behind every OutboxClient connection.

    OutboxClient opens a fresh connection per operation, so a bare ``:memory:``
    would give each operation a different empty database. Route those connects
    to one named shared-cache database kept alive by ``keeper``; ``backup`` can
    snapshot it to a pytest ``tmp_path`` file for the read-only CLIs. No live
    database is ever opened, and no file named ``:memory:`` is created.
    """
    name = f"assistx-quarantine-marker-r18-{uuid.uuid4().hex}"
    uri = f"file:{name}?mode=memory&cache=shared"
    real_connect = sqlite3.connect
    keeper = real_connect(uri, uri=True)
    keeper.executescript(SCHEMA)

    class SqliteShim:
        Error = sqlite3.Error
        Connection = sqlite3.Connection

        @staticmethod
        def connect(database, *args, **kwargs):
            if database != MEM_PATH:
                return real_connect(database, *args, **kwargs)
            kwargs["uri"] = True
            return real_connect(uri, *args, **kwargs)

    # Patch only the module under test, never the global sqlite3 module.
    monkeypatch.setattr(oc, "sqlite3", SqliteShim)

    class MemOutbox:
        def client(self, **kwargs):
            return OutboxClient(
                db_path=MEM_PATH,
                api_url="http://assistx.invalid",
                auto_flush=False,
                **kwargs,
            )

        def insert(
            self,
            outbox_id,
            *,
            created_at=FRESH,
            status="pending",
            attempt_count=0,
            claim_owner=None,
            claimed_at=None,
        ):
            keeper.execute(
                "INSERT INTO outbox (outbox_id, event_id, payload_json, attempt_count, "
                "last_attempt_at, status, created_at, claim_owner, claimed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    outbox_id,
                    f"evt-{outbox_id}",
                    PAYLOAD,
                    attempt_count,
                    None,
                    status,
                    created_at,
                    claim_owner,
                    claimed_at,
                ),
            )
            keeper.commit()

        def row(self, outbox_id):
            return keeper.execute(
                "SELECT status, attempt_count, claim_owner, claimed_at FROM outbox WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()

        def backup(self, path: Path) -> None:
            target = real_connect(path)
            try:
                keeper.backup(target)
                target.commit()
            finally:
                target.close()

    assert not os.path.lexists(MEM_PATH)
    try:
        yield MemOutbox()
    finally:
        keeper.close()
        assert not os.path.lexists(MEM_PATH)


def _seed_three_rows(mem_outbox) -> None:
    """Oldest row carries the R4 hold; two newer rows are normally claimable."""
    mem_outbox.insert(
        "held",
        created_at=FRESH,
        status="pending",
        attempt_count=9,
        claim_owner=R4_MARKER,
    )
    mem_outbox.insert("normal", created_at="2026-11-27T11:00:01+00:00", status="pending")
    mem_outbox.insert(
        "opaque",
        created_at="2026-11-27T11:00:02+00:00",
        status="pending",
        claim_owner=OPAQUE_OWNER,
    )


def test_r4_marker_is_verbatim_from_reconciliation_report():
    """The constant under test is the exact label the R4 report records."""
    assert R4_MARKER == "quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126"
    if not R4_REPORT.exists():
        pytest.skip("R4 reconciliation report is not present in this environment")
    text = R4_REPORT.read_text(encoding="utf-8")
    assert f"SET claim_owner = '{R4_MARKER}'" in text


def test_r11_guard_pattern_matches_r4_marker_not_normal_owner():
    """The production guard's LIKE pattern accepts the marker, not a normal owner."""
    # Tie the assertion to the production constant, not to a copied string.
    assert "LIKE 'quarantine:%'" in oc._QUARANTINE_CLAIM_GUARD

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE outbox (claim_owner TEXT)")
        conn.executemany(
            "INSERT INTO outbox VALUES (?)",
            [(R4_MARKER,), (OPAQUE_OWNER,), (None,)],
        )
        # Exactly the pattern quoted in the R11 report.
        matched = [
            row[0] for row in conn.execute("SELECT claim_owner FROM outbox WHERE claim_owner LIKE 'quarantine:%'")
        ]
        assert matched == [R4_MARKER]
        assert conn.execute("SELECT ? LIKE 'quarantine:%'", (R4_MARKER,)).fetchone()[0] == 1
        assert conn.execute("SELECT ? LIKE 'quarantine:%'", (OPAQUE_OWNER,)).fetchone()[0] == 0

        # The same pattern as it is actually embedded in the claim guard: the
        # marker is excluded while NULL and opaque-token owners stay claimable.
        guarded = [row[0] for row in conn.execute(f"SELECT claim_owner FROM outbox WHERE {oc._QUARANTINE_CLAIM_GUARD}")]
        assert set(guarded) == {None, OPAQUE_OWNER}
        assert R4_MARKER not in guarded
    finally:
        conn.close()


def test_health_quarantined_and_audit_owner_label_agree_on_r4_marker(mem_outbox, tmp_path: Path) -> None:
    """Both read-only tools recognize the same R4 marker on the same fixture."""
    _seed_three_rows(mem_outbox)
    path = tmp_path / "marker-contract.sqlite"
    mem_outbox.backup(path)

    before = _digest(path)
    audit_result = _run(AUDIT, str(path), "--json")
    health_result = _run(HEALTH, str(path), "--now", NOW)
    assert audit_result.returncode == 0, audit_result.stderr
    assert health_result.returncode == 0, health_result.stderr
    audit = _one_json(audit_result)
    health = _one_json(health_result)

    # The audit's owner label is the exact printable marker; opaque owners and
    # NULL owners keep their existing redaction contract.
    audit_mod = _audit_module()
    assert audit_mod._QUARANTINE_PREFIX == "quarantine:"
    assert audit_mod._owner_label(R4_MARKER) == R4_MARKER
    assert audit_mod._owner_label(OPAQUE_OWNER) == "<redacted>"
    assert audit_mod._owner_label(None) == "NULL"
    assert audit["claim_owner_counts"] == {R4_MARKER: 1, "NULL": 1, "<redacted>": 1}
    assert audit["pass"] is True

    # The health CLI does not print the label but counts the same row, and keeps
    # it out of claimable_pending; the two tools agree on the marker total.
    assert R4_MARKER in audit_result.stdout
    assert R4_MARKER not in health_result.stdout + health_result.stderr
    assert health["quarantined"] == 1
    assert audit["claim_owner_counts"][R4_MARKER] == health["quarantined"]
    assert health["pending"] == 3
    assert health["claimable_pending"] == 2
    assert health["claimable_pending"] + health["quarantined"] == health["pending"]
    assert health["total"] == 3
    assert health["query_only"] == 1
    assert health["readable"] is True
    assert health["status"] == "ok"
    assert health["reason"] is None

    # Read-only: both CLIs left the snapshot byte-identical, no sidecars.
    assert before == _digest(path)
    assert list(tmp_path.glob("marker-contract.sqlite-*")) == []


def test_marked_row_is_not_claimed_but_quarantined_not_claimable_pending(mem_outbox, tmp_path: Path) -> None:
    """One R4-marked row is skipped by the claim path and excluded from claimable."""
    _seed_three_rows(mem_outbox)
    path = tmp_path / "pre-claim.sqlite"
    mem_outbox.backup(path)

    # Health on the pre-claim snapshot: the marked pending row is quarantined,
    # never counted as claimable_pending, while the two normal rows are.
    health_result = _run(HEALTH, str(path), "--now", NOW)
    assert health_result.returncode == 0, health_result.stderr
    health = _one_json(health_result)
    assert health["pending"] == 3
    assert health["quarantined"] == 1
    assert health["claimable_pending"] == 2
    assert health["claimable_pending"] + health["quarantined"] == health["pending"]

    client = mem_outbox.client()
    assert client._claim_next().outbox_id == "normal"
    assert client._claim_next().outbox_id == "opaque"
    # The marked row is the only one left and is still never claimable, even
    # though it is the oldest pending row in the queue.
    assert client._claim_next() is None
    assert mem_outbox.row("held") == ("pending", 9, R4_MARKER, None)

    # The expired-lease recovery path uses the same guard: a marked row with a
    # stale claimed_at is still not recovered.
    mem_outbox.insert(
        "held-stale",
        created_at="2026-11-27T10:00:00+00:00",
        status="claimed",
        claim_owner=R4_MARKER,
        claimed_at="2026-11-27T09:00:00+00:00",
    )
    assert client._claim_next() is None
    assert mem_outbox.row("held-stale")[0:2] == ("claimed", 0)
    assert mem_outbox.row("held-stale")[2] == R4_MARKER
