"""Hermetic regression tests: a backup chain must carry a database identity.

All inspection output is synthetic JSON produced in-process; the runner is a
fake callable. No neo4j-admin binary, backup file, database, service, credential
or network mount is used.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from assistx.neo4j_backup_verification import (
    BackupVerificationError,
    Neo4jBackupVerifier,
)

_VALID_ID = "12345678-1234-1234-1234-123456789abc"
_OTHER_ID = "aaaaaaaa-1234-1234-1234-123456789abc"


def _row(path, *, full, low, high, database_id=None):
    row = {
        "path": path,
        "database": "neo4j",
        "time": "1970-01-01T00:16:40Z",
        "full": full,
        "lowestTransactionId": low,
        "highestTransactionId": high,
        "recovered": full,
    }
    if database_id is not None:
        row["databaseId"] = database_id
    return row


def _verifier(rows):
    def runner(_command, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(rows),
            stderr="",
        )

    # Latest backup is at 1_000_000 ms; pin the clock exactly there so RPO is 0.
    return Neo4jBackupVerifier(runner, clock_ms=lambda: 1_000_000)


def test_verify_rejects_chain_with_no_database_identity():
    """Missing databaseId on every artifact must fail closed, not verify."""
    rows = [
        _row("full.backup", full=True, low=1, high=10),
        _row("diff.backup", full=False, low=11, high=14),
    ]
    with pytest.raises(BackupVerificationError, match="database identity"):
        _verifier(rows).verify("/backups", "neo4j", max_age_seconds=60)


def test_verify_rejects_blank_database_identity():
    """Empty / whitespace-only identifiers are not an identity either."""
    rows = [
        _row("full.backup", full=True, low=1, high=10, database_id=""),
        _row("diff.backup", full=False, low=11, high=14, database_id="   "),
    ]
    with pytest.raises(BackupVerificationError, match="database identity"):
        _verifier(rows).verify("/backups", "neo4j", max_age_seconds=60)


def test_verify_accepts_single_database_identity():
    """Regression guard: a well-formed single-ID chain still verifies."""
    rows = [
        _row("full.backup", full=True, low=1, high=10, database_id=_VALID_ID),
        _row("diff.backup", full=False, low=11, high=14, database_id=_VALID_ID),
    ]
    result = _verifier(rows).verify("/backups", "neo4j", max_age_seconds=60)
    assert result["ok"] is True
    assert result["database_id"] == _VALID_ID


def test_verify_still_rejects_mixed_database_identities():
    """Regression guard: the existing multi-ID rejection stays intact."""
    rows = [
        _row("full.backup", full=True, low=1, high=10, database_id=_VALID_ID),
        _row("diff.backup", full=False, low=11, high=14, database_id=_OTHER_ID),
    ]
    with pytest.raises(BackupVerificationError, match="multiple database IDs"):
        _verifier(rows).verify("/backups", "neo4j", max_age_seconds=60)
