"""Round 42: backup artifact boolean flags must fail closed on string spellings.

Pure in-process tests: no subprocess, network, Neo4j, DB, WAL, credential or
live outbox access.  A stub runner supplies canned ``neo4j-admin`` output.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from assistx.neo4j_backup_verification import (
    BackupVerificationError,
    Neo4jBackupVerifier,
)


def _row(path: str, **overrides):
    row = {
        "path": path,
        "database": "neo4j",
        "databaseId": "12345678-1234-1234-1234-123456789abc",
        "time": "1970-01-01T00:16:40Z",
        "full": True,
        "lowestTransactionId": 1,
        "highestTransactionId": 10,
        "recovered": True,
    }
    row.update(overrides)
    return row


def _parse(rows):
    return Neo4jBackupVerifier._parse_output(json.dumps(rows), "neo4j")


def _verifier(rows):
    return Neo4jBackupVerifier(
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(rows),
            stderr="",
        ),
        clock_ms=lambda: 1_000_000,
    )


def test_string_false_full_flag_is_not_parsed_as_full():
    artifacts = _parse([_row("diff.backup", full="false")])
    assert artifacts[0].full is False


def test_string_false_recovered_flag_is_not_parsed_as_recovered():
    artifacts = _parse([_row("full.backup", recovered="false")])
    assert artifacts[0].recovered is False


def test_diff_only_chain_with_string_false_full_is_rejected():
    verifier = _verifier([_row("diff.backup", full="false")])
    with pytest.raises(BackupVerificationError, match="does not begin with a full backup"):
        verifier.verify("/backups", "neo4j", max_age_seconds=60)


def test_require_recovered_rejects_string_false_recovered():
    verifier = _verifier([_row("full.backup", recovered="false")])
    with pytest.raises(BackupVerificationError, match="not recovered/aggregated"):
        verifier.verify(
            "/backups", "neo4j", max_age_seconds=60, require_recovered=True
        )


def test_genuine_boolean_full_chain_still_verifies():
    rows = [
        _row("full.backup", full=True, lowestTransactionId=1, highestTransactionId=10),
        _row(
            "diff.backup",
            full=False,
            recovered=False,
            lowestTransactionId=11,
            highestTransactionId=14,
        ),
    ]
    result = _verifier(rows).verify("/backups", "neo4j", max_age_seconds=60)
    assert result["ok"] is True
    assert result["highest_tx"] == 14


def test_genuine_boolean_recovered_still_passes():
    result = _verifier(
        [_row("full.backup", full=True, recovered=True)]
    ).verify("/backups", "neo4j", max_age_seconds=60, require_recovered=True)
    assert result["ok"] is True
    assert result["recovered"] is True
