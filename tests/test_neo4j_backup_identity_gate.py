"""Synthetic backup-name/UUID authorization gate; no archive or graph reads."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from assistx.neo4j_backup_verification import (
    BackupVerificationError,
    Neo4jBackupVerifier,
)

DB_ID = "12345678-1234-1234-1234-123456789abc"
WRONG_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
GOOD_TIME = "1970-01-01T00:16:40Z"


def artifact(*, database="assistx", database_id=DB_ID, path="neo4j-archive.backup"):
    result = {
        "path": path, "time": GOOD_TIME, "full": True,
        "lowestTransactionId": 1, "highestTransactionId": 17,
    }
    if database is not None:
        result["database"] = database
    if database_id is not None:
        result["databaseId"] = database_id
    return result


def verifier_for(rows):
    def runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr="")
    return Neo4jBackupVerifier(runner, clock_ms=lambda: 1_000_000)


def test_explicit_assistx_database_metadata_positive():
    result = verifier_for([artifact()]).verify("/synthetic", "assistx", max_age_seconds=60)
    assert result["database"] == "assistx"
    assert result["database_id"] == DB_ID
    assert result["live_database_identity_pinned"] is False
    assert result["database_restore_certified"] is False


def test_match_pinned_live_database_uuid_positive():
    result = verifier_for([artifact()]).verify(
        "/synthetic", "assistx", expected_database_id=DB_ID, max_age_seconds=60
    )
    assert result["live_database_identity_pinned"] is True


@pytest.mark.parametrize(
    "rows",
    [
        [artifact(database=None)],
        [artifact(database="neo4j")],
        [artifact(database=17)],
        [artifact(database="")],
    ],
)
def test_synthetic_filename_or_missing_database_cannot_impersonate_assistx(rows):
    with pytest.raises(BackupVerificationError, match="no parseable artifacts"):
        verifier_for(rows).verify("/synthetic", "assistx", max_age_seconds=60)


@pytest.mark.parametrize("database_id", [None, "", "wrong-uuid"])
def test_missing_database_id_or_wrong_pinned_id_rejected(database_id):
    verifier = verifier_for([artifact(database_id=database_id)])
    if not database_id:
        with pytest.raises(BackupVerificationError, match="database ID is missing"):
            verifier.verify("/synthetic", "assistx", max_age_seconds=60)
    else:
        with pytest.raises(BackupVerificationError, match="does not match pinned"):
            verifier.verify(
                "/synthetic", "assistx", expected_database_id=WRONG_ID,
                max_age_seconds=60,
            )


def test_wrong_owner_pin_denied_even_for_syntactically_valid_archive():
    with pytest.raises(BackupVerificationError, match="does not match pinned"):
        verifier_for([artifact()]).verify(
            "/synthetic", "assistx", expected_database_id=WRONG_ID,
            max_age_seconds=60,
        )


def test_legacy_table_filename_contains_assistx_but_database_is_neo4j():
    body = (
        "| FILE | DATABASE | DATABASE ID | TIME | FULL | COMPRESSED "
        "| LOWEST TX | HIGHEST TX |\n"
        "| file:///backups/assistx-full.backup | neo4j | " + DB_ID + " | "
        + GOOD_TIME + " | true | true | 1 | 8 |"
    )
    assert Neo4jBackupVerifier._parse_table(body, "assistx") == []


def test_legacy_table_matching_explicit_column_accepts_correct_db():
    body = (
        "| FILE | DATABASE | DATABASE ID | TIME | FULL | COMPRESSED "
        "| LOWEST TX | HIGHEST TX |\n"
        "| file:///backups/neo4j-full.backup | assistx | " + DB_ID + " | "
        + GOOD_TIME + " | true | true | 1 | 8 |"
    )
    rows = Neo4jBackupVerifier._parse_table(body, "assistx")
    assert len(rows) == 1 and rows[0].database == "assistx"
    assert rows[0].database_id == DB_ID


def test_mixed_chain_database_ids_fail_even_if_names_match():
    with pytest.raises(BackupVerificationError, match="multiple database IDs"):
        verifier_for([
            artifact(path="full.backup"),
            {**artifact(path="diff.backup", database_id=WRONG_ID),
             "full": False, "lowestTransactionId": 18,
             "highestTransactionId": 19},
        ]).verify("/synthetic", "assistx", max_age_seconds=60)


def test_cli_never_defaults_to_neo4j_when_database_not_explicitly_given():
    script = Path(__file__).resolve().parents[1] / "scripts/verify-neo4j-backup.py"
    spec = importlib.util.spec_from_file_location("isolated_backup_cli", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser = module.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["/synthetic"])
    assert exc.value.code == 2
    chosen = parser.parse_args([
        "/synthetic", "--database", "assistx", "--expected-database-id", DB_ID
    ])
    assert chosen.database == "assistx"
    assert chosen.expected_database_id == DB_ID


def test_archive_metadata_tests_never_access_real_backup_files(monkeypatch):
    def forbid(*_args, **_kwargs):
        raise AssertionError("filesystem read should not occur")
    monkeypatch.setattr(Path, "open", forbid)
    assert verifier_for([artifact()]).verify(
        "/fake-path-that-does-not-exist", "assistx", max_age_seconds=60
    )["database_restore_certified"] is False
