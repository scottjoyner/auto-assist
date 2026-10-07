import hashlib
import json
from pathlib import Path

import pytest

from assistx.ripwire_adapter import RipwireAdapter


def _fake_ripwire(tmp_path: Path, *, gate_exit: int = 0) -> Path:
    binary = tmp_path / "ripwire"
    binary.write_text(
        f'''#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "ripwire 0.6.5 (fixture)"
  exit 0
fi
case "$*" in
  *--test-gate*) echo '{{"gate":"fixture"}}'; exit {gate_exit} ;;
  *) echo '{{"nodes":[{{"id":"fixture"}}]}}'; exit 0 ;;
esac
'''
    )
    binary.chmod(0o755)
    return binary


def test_orient_is_bounded_and_persists_raw_evidence(tmp_path):
    binary = _fake_ripwire(tmp_path)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    repo = tmp_path / "repo"
    repo.mkdir()
    result = RipwireAdapter(
        str(binary), expected_version="0.6.5", expected_sha256=digest
    ).orient(
        str(repo), "fix notification grouping",
        evidence_dir=str(tmp_path / "evidence"), max_tokens=900,
    )

    assert result.status == "ok"
    assert result.binary.version == "0.6.5"
    assert "--for=fix notification grouping" in result.command
    assert "--token-budget=900" in result.command
    assert Path(result.stdout_path).read_bytes() == b'{"nodes":[{"id":"fixture"}]}\n'
    assert result.payload == {"nodes": [{"id": "fixture"}]}
    metadata = json.loads(Path(result.metadata_path).read_text())
    raw = Path(result.stdout_path).read_bytes()
    assert metadata["stdout_sha256"] == hashlib.sha256(raw).hexdigest()


def test_test_gate_preserves_findings_exit_four(tmp_path):
    binary = _fake_ripwire(tmp_path, gate_exit=4)
    repo = tmp_path / "repo"
    repo.mkdir()
    result = RipwireAdapter(str(binary), expected_version="0.6.5").test_gate(
        str(repo), evidence_dir=str(tmp_path / "evidence")
    )
    assert result.exit_code == 4
    assert result.status == "findings"
    assert result.payload == {"gate": "fixture"}


def test_binary_digest_mismatch_fails_closed(tmp_path):
    binary = _fake_ripwire(tmp_path)
    with pytest.raises(RuntimeError, match="SHA-256"):
        RipwireAdapter(str(binary), expected_sha256="0" * 64).verify_binary()
