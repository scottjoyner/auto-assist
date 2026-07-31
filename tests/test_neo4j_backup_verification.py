from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from assistx.neo4j_backup_verification import (
    BackupVerificationError,
    Neo4jBackupVerifier,
)


def artifact(path, *, full, low, high, time="1970-01-01T00:16:40Z"):
    return {
        "path": path,
        "database": "neo4j",
        "databaseId": "12345678-1234-1234-1234-123456789abc",
        "time": time,
        "full": full,
        "lowestTransactionId": low,
        "highestTransactionId": high,
        "recovered": full,
    }


def test_current_inspect_command_validates_contiguous_chain():
    rows = [
        artifact("full.backup", full=True, low=1, high=10),
        artifact("diff.backup", full=False, low=11, high=14),
    ]
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr="")

    verifier = Neo4jBackupVerifier(runner, clock_ms=lambda: 1_000_000)
    result = verifier.verify("/backups", "neo4j", max_age_seconds=60)

    assert calls[0][:3] == ["neo4j-admin", "backup", "inspect"]
    assert result["ok"] is True
    assert result["highest_tx"] == 14
    assert result["artifact_count"] == 2
    assert result["restore_command"][-1] == "neo4j"


def test_legacy_5x_inspection_is_used_when_current_command_is_unavailable():
    table = """
| FILE | DATABASE | DATABASE ID | TIME | FULL | COMPRESSED | LOWEST TX | HIGHEST TX |
| file:///backups/neo4j-full.backup | neo4j | 12345678-1234-1234-1234-123456789abc | 1970-01-01T00:16:40Z | true | true | 1 | 8 |
"""
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        if command[1:3] == ["backup", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="unknown command")
        return SimpleNamespace(returncode=0, stdout=table, stderr="")

    verifier = Neo4jBackupVerifier(runner, clock_ms=lambda: 1_000_000)
    result = verifier.verify("/backups", "neo4j", max_age_seconds=60)

    assert calls[1][:3] == ["neo4j-admin", "database", "backup"]
    assert result["highest_tx"] == 8


def test_backup_chain_gap_fails_closed():
    rows = [
        artifact("full.backup", full=True, low=1, high=10),
        artifact("diff.backup", full=False, low=14, high=15),
    ]
    verifier = Neo4jBackupVerifier(
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(rows),
            stderr="",
        ),
        clock_ms=lambda: 1_000_000,
    )

    with pytest.raises(BackupVerificationError, match="transaction chain has a gap"):
        verifier.verify("/backups", "neo4j", max_age_seconds=60)


def test_consistency_check_failure_blocks_backup_attestation():
    verifier = Neo4jBackupVerifier(
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="consistency failure",
        )
    )
    with pytest.raises(BackupVerificationError, match="consistency check failed"):
        verifier.run_consistency_check("/backups/aggregated.backup", "neo4j")
