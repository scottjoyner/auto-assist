import hashlib
from pathlib import Path

import pytest

from assistx.cass_session_search import CassSessionSearch


def _fake_cass(tmp_path: Path, *, partial: bool = False) -> Path:
    binary = tmp_path / "cass"
    budget = 'true' if partial else 'false'
    binary.write_text(
        f'''#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "cass 0.10.0"
  exit 0
fi
if [ "$1" = "selftest" ]; then
  echo '{{"status":"ok","functional":true,"archive_accessed":false}}'
  exit 0
fi
if [ "$1" = "search" ]; then
  echo '{{"hits":[],"budget":{{"timed_out":{budget}}}}}'
  exit 0
fi
exit 2
'''
    )
    binary.chmod(0o755)
    return binary


def test_search_forces_strict_read_only_flags_and_persists_evidence(tmp_path):
    binary = _fake_cass(tmp_path)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    result = CassSessionSearch(
        str(binary), expected_version="0.10.0", expected_sha256=digest
    ).search(
        "notification crash", workspace=str(workspace),
        evidence_dir=str(tmp_path / "evidence"), days=3, limit=4,
    )

    assert result.status == "ok"
    assert "--no-maintenance" in result.command
    assert "--mode" in result.command and "lexical" in result.command
    assert "--workspace" in result.command
    assert "--json" in result.command
    assert result.binary.selftest_status == "ok"
    assert Path(result.stdout_path).read_text().startswith('{"hits"')


def test_budget_timeout_is_partial_not_empty_proof(tmp_path):
    binary = _fake_cass(tmp_path, partial=True)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    result = CassSessionSearch(str(binary)).search(
        "anything", workspace=str(workspace),
        evidence_dir=str(tmp_path / "evidence"),
    )
    assert result.status == "partial-timeout"


def test_cass_digest_mismatch_fails_closed(tmp_path):
    binary = _fake_cass(tmp_path)
    with pytest.raises(RuntimeError, match="SHA-256"):
        CassSessionSearch(str(binary), expected_sha256="f" * 64).verify_binary()
