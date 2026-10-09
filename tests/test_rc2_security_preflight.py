"""Offline, metadata-only checks for RC2 release custody preflight."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "rc2-security-preflight.py"
spec = importlib.util.spec_from_file_location("rc2_security_preflight", SCRIPT)
assert spec is not None and spec.loader is not None
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=True,
    )


@pytest.fixture
def synthetic_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.name=Synthetic",
         "-c", "user.email=synthetic@example.invalid",
         "commit", "-q", "--allow-empty", "-m", "initial")
    (tmp_path / ".env.example").write_text(
        "ASSISTX_API_BIND=127.0.0.1\n", encoding="utf-8"
    )
    (tmp_path / ".env.reconciliation.example").write_text(
        "SYNTHETIC_PLACEHOLDER=\n", encoding="utf-8"
    )
    (tmp_path / ".env.bak-synthetic").write_text(
        "SYNTHETIC_PLACEHOLDER=\n", encoding="utf-8"
    )
    _git(tmp_path, "add", "--", ".env.example",
         ".env.reconciliation.example", ".env.bak-synthetic")
    _git(tmp_path, "-c", "user.name=Synthetic",
         "-c", "user.email=synthetic@example.invalid",
         "commit", "-q", "-m", "synthetic tracked paths")
    return tmp_path


def test_only_explicit_archive_and_single_template_are_exempt() -> None:
    assert preflight.tracked_env_paths([
        ".env.example",
        "archive/.env.committed-SECRETS-REMOVED",
        ".env.reconciliation.example",
        "nested/.env.override",
        ".env.bak-synthetic",
    ]) == [
        ".env.bak-synthetic",
        ".env.reconciliation.example",
        "nested/.env.override",
    ]


def test_index_failure_does_not_inspect_values(
    synthetic_repo: pathlib.Path, capsys: pytest.CaptureFixture[str],
) -> None:
    result = preflight.inspect(synthetic_repo)
    assert result["tracked_environment_count"] == 2
    assert result["index_custody"] == "HOLD"
    assert result["production_release_authorized"] is False
    assert set(result["external_gates"].values()) == {"UNVERIFIED"}
    assert "SYNTHETIC_PLACEHOLDER" not in json.dumps(result)
    assert capsys.readouterr().out == ""
    assert preflight.main(["--repo", str(synthetic_repo), "--index-only"]) == 2


def test_clean_index_is_not_approval(synthetic_repo: pathlib.Path) -> None:
    _git(synthetic_repo, "rm", "-q", "--cached",
         ".env.bak-synthetic", ".env.reconciliation.example")
    index_result = preflight.inspect(synthetic_repo)
    assert index_result["index_custody"] == "PASS_INDEX_ONLY"
    assert index_result["production_release_authorized"] is False
    assert preflight.main(["--repo", str(synthetic_repo), "--index-only"]) == 0
    assert preflight.main(["--repo", str(synthetic_repo)]) == 2


def test_missing_git_inventory_fails_closed(tmp_path: pathlib.Path) -> None:
    assert preflight.main(["--repo", str(tmp_path)]) == 2


def test_exact_head_reported_without_git_remote_urls(
    synthetic_repo: pathlib.Path,
) -> None:
    result = preflight.inspect(synthetic_repo)
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=synthetic_repo,
        text=True,
    ).strip()
    assert result["source_head"] == sha
    assert "remote" not in json.dumps(result)
    assert len(sha) == 40
