"""Hermetic edge coverage for the Neo4j backup receipt verifier (round 6).

These tests exercise *absent* and *corrupt* inspection output plus the
fail-closed chain/RPO/recovered checks that the existing happy-path tests do
not cover. Everything uses a fake ``runner``: no neo4j-admin binary, no live
service, no database, no network and no credentials are involved.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from assistx.neo4j_backup_verification import (
    BackupVerificationError,
    Neo4jBackupVerifier,
)

_DB_ID = "12345678-1234-1234-1234-123456789abc"


def _artifact(path, *, full, low, high, time="1970-01-01T00:16:40Z", db_id=_DB_ID, recovered=None):
    return {
        "path": path,
        "database": "neo4j",
        "databaseId": db_id,
        "time": time,
        "full": full,
        "lowestTransactionId": low,
        "highestTransactionId": high,
        "recovered": (full if recovered is None else recovered),
    }


def _runner(stdout="", *, returncode=0, stderr=""):
    """A stateless fake runner that answers every command identically."""
    calls: list[list[str]] = []

    def run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    return run, calls


def test_absent_backup_receipt_when_inspection_command_is_unavailable():
    """No inspect binary -> fail closed with the command's own error, not a false OK."""
    run, calls = _runner(returncode=1, stderr="neo4j-admin: command not found")
    verifier = Neo4jBackupVerifier(run, clock_ms=lambda: 1_000_000)

    with pytest.raises(BackupVerificationError, match="command not found"):
        verifier.verify("/backups", "neo4j")

    # Both supported inspection forms were attempted before giving up.
    assert len(calls) == 2


def test_absent_backup_receipt_when_inspection_returns_no_artifacts():
    """A zero exit with empty output is still 'no receipt', never a pass."""
    run, _ = _runner(stdout="")
    verifier = Neo4jBackupVerifier(run, clock_ms=lambda: 1_000_000)

    with pytest.raises(BackupVerificationError, match="no parseable artifacts"):
        verifier.verify("/backups", "neo4j")


def test_corrupt_inspection_output_fails_closed():
    """Malformed JSON that is also not a table yields no artifacts -> fail closed."""
    run, _ = _runner(stdout="{not-json-and-not-a-table")
    verifier = Neo4jBackupVerifier(run, clock_ms=lambda: 1_000_000)

    with pytest.raises(BackupVerificationError, match="no parseable artifacts"):
        verifier.verify("/backups", "neo4j")


def test_corrupt_json_rows_of_the_wrong_database_are_not_artifacts():
    """Non-dict rows and rows for another database cannot satisfy this database."""
    rows = [
        "not-a-dict",
        _artifact("other.backup", full=True, low=1, high=2),
    ]
    rows[1]["database"] = "another-db"
    run, _ = _runner(stdout=json.dumps(rows))
    verifier = Neo4jBackupVerifier(run, clock_ms=lambda: 1_000_000)

    with pytest.raises(BackupVerificationError, match="no parseable artifacts"):
        verifier.verify("/backups", "neo4j")


def test_corrupt_current_output_falls_back_to_legacy_inspection():
    """A garbled current-format receipt still permits the legacy 5.x form."""
    table = (
        "| FILE | DATABASE | DATABASE ID | TIME | FULL | COMPRESSED "
        "| LOWEST TX | HIGHEST TX |\n"
        f"| file:///backups/neo4j-full.backup | neo4j | {_DB_ID} "
        "| 1970-01-01T00:16:40Z | true | true | 1 | 8 |\n"
    )
    calls: list[list[str]] = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[1:3] == ["backup", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="{garbled", stderr="")
        return SimpleNamespace(returncode=0, stdout=table, stderr="")

    verifier = Neo4jBackupVerifier(run, clock_ms=lambda: 1_000_000)
    result = verifier.verify("/backups", "neo4j", max_age_seconds=60)

    assert result["highest_tx"] == 8
    assert calls[1][:3] == ["neo4j-admin", "database", "backup"]


def test_chain_not_beginning_with_full_backup_fails_closed():
    run, _ = _runner(
        stdout=json.dumps(
            [
                _artifact("diff.backup", full=False, low=1, high=4),
            ]
        )
    )
    verifier = Neo4jBackupVerifier(run, clock_ms=lambda: 1_000_000)

    with pytest.raises(BackupVerificationError, match="does not begin with a full backup"):
        verifier.verify("/backups", "neo4j")


def test_chain_with_multiple_database_ids_fails_closed():
    run, _ = _runner(
        stdout=json.dumps(
            [
                _artifact("full.backup", full=True, low=1, high=4, db_id=_DB_ID),
                _artifact("diff.backup", full=False, low=5, high=6, db_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            ]
        )
    )
    verifier = Neo4jBackupVerifier(run, clock_ms=lambda: 1_000_000)

    with pytest.raises(BackupVerificationError, match="multiple database IDs"):
        verifier.verify("/backups", "neo4j")


def test_transaction_ids_moving_backwards_fail_closed():
    run, _ = _runner(
        stdout=json.dumps(
            [
                _artifact("full.backup", full=True, low=1, high=10),
                _artifact("diff.backup", full=False, low=11, high=9),
            ]
        )
    )
    verifier = Neo4jBackupVerifier(run, clock_ms=lambda: 1_000_000)

    with pytest.raises(BackupVerificationError, match="moved backwards"):
        verifier.verify("/backups", "neo4j")


def test_backup_older_than_rpo_fails_closed():
    rows = [_artifact("full.backup", full=True, low=1, high=10)]
    run, _ = _runner(stdout=json.dumps(rows))
    # Backup is at t=1000s; clock is 200s later with a 60s RPO.
    verifier = Neo4jBackupVerifier(run, clock_ms=lambda: 1_200_000)

    with pytest.raises(BackupVerificationError, match="older than the allowed RPO"):
        verifier.verify("/backups", "neo4j", max_age_seconds=60)


def test_missing_timestamp_fails_closed_even_with_zero_rpo():
    rows = [_artifact("full.backup", full=True, low=1, high=10, time="")]
    run, _ = _runner(stdout=json.dumps(rows))
    verifier = Neo4jBackupVerifier(run, clock_ms=lambda: 1_000_000)

    with pytest.raises(BackupVerificationError, match="timestamp is invalid"):
        verifier.verify("/backups", "neo4j", max_age_seconds=0)


def test_future_dated_backup_fails_closed():
    rows = [_artifact("full.backup", full=True, low=1, high=10)]
    run, _ = _runner(stdout=json.dumps(rows))
    # Clock is behind the backup timestamp -> negative age must not pass.
    verifier = Neo4jBackupVerifier(run, clock_ms=lambda: 0)

    with pytest.raises(BackupVerificationError, match="timestamp is invalid"):
        verifier.verify("/backups", "neo4j")


def test_require_recovered_rejects_an_unaggregated_latest_backup():
    rows = [
        _artifact("full.backup", full=True, low=1, high=10, recovered=True),
        _artifact("diff.backup", full=False, low=11, high=12, recovered=False),
    ]
    run, _ = _runner(stdout=json.dumps(rows))
    verifier = Neo4jBackupVerifier(run, clock_ms=lambda: 1_000_000)

    with pytest.raises(BackupVerificationError, match="not recovered/aggregated"):
        verifier.verify("/backups", "neo4j", require_recovered=True)


def test_verification_never_runs_a_mutating_command():
    """verify() only inspects; it must not restore, check or aggregate a backup."""
    rows = [_artifact("full.backup", full=True, low=1, high=10)]
    run, calls = _runner(stdout=json.dumps(rows))
    verifier = Neo4jBackupVerifier(run, clock_ms=lambda: 1_000_000)

    assert verifier.verify("/backups", "neo4j")["ok"] is True
    assert calls, "expected inspect commands"
    for command in calls:
        joined = " ".join(command)
        assert "restore" not in joined
        assert "aggregate" not in joined
        assert "check" not in joined
