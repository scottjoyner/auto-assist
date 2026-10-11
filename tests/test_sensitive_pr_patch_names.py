"""Synthetic negative coverage for metadata-only public sensitive-diff guard."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "audit_sensitive_pr_patch_names.py"
spec = importlib.util.spec_from_file_location("public_guard", SOURCE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_unrelated_change_passes_without_custody_claim():
    r = guard.classify(b"M\0src/assistx/api.py\0")
    assert r["status"] == "PASS_NO_ENV_DIFF"
    assert r["credential_custody_closed"] is False


def test_no_changes_pass():
    assert guard.classify(b"\0")["status"] == "PASS_NO_ENV_DIFF"


@pytest.mark.parametrize("path", [
    ".env", ".env.reconciliation.example", ".env.bak-sync-20261006T051130Z",
    "services/.env.local", "nested/.ENV.prod", ".env-backup",
])
@pytest.mark.parametrize("status", ["A", "M", "D"])
def test_sensitive_filename_changes_fail(path, status):
    data = status.encode() + b"\0" + path.encode() + b"\0"
    r = guard.classify(data)
    assert r["status"] == "HOLD_PUBLIC_ENV_DIFF"
    assert r["sensitive_status_counts"] == {status: 1}
    assert path not in str(r)


def test_historical_two_deletions_remain_hold():
    r = guard.classify(
        b"D\0.env.bak-sync-20261006T051130Z\0D\0.env.reconciliation.example\0"
    )
    assert r["sensitive_change_count"] == 2
    assert r["sensitive_status_counts"] == {"D": 2}
    assert r["reads_file_contents"] is False


@pytest.mark.parametrize("value", [b"", b"M\0oops", b"M\0", b"bad\0", b"\0name\0"])
def test_invalid_metadata_fails_closed(value):
    with pytest.raises(ValueError):
        guard.classify(value)


def test_invalid_sha_never_runs_git():
    with pytest.raises(ValueError):
        guard.audit("not-a-sha", "123")


# Temporary synthetic repositories contain placeholder-only file bytes.
# These tests never open, hash, print, or copy production credential blobs.
def _git(repo, *args):
    import subprocess
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
        timeout=8,
    )
    return result.stdout.strip()


@pytest.fixture
def history_repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Synthetic Test")
    _git(tmp_path, "config", "user.email", "synthetic@invalid.example")
    (tmp_path / "README.md").write_text("public synthetic fixture\\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-qm", "baseline")
    base = _git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)
    return tmp_path, base


def test_final_diff_clean_but_intermediate_env_add_delete_still_holds(history_repo):
    repo, base = history_repo
    (repo / ".env.synthetic").write_text("SYNTHETIC_PLACEHOLDER_ONLY\\n")
    _git(repo, "add", "-f", ".env.synthetic")
    _git(repo, "commit", "-qm", "synthetic add")
    _git(repo, "rm", "-q", ".env.synthetic")
    _git(repo, "commit", "-qm", "synthetic delete")
    head = _git(repo, "rev-parse", "HEAD")

    report = guard.audit(base, head)
    assert report["net_sensitive_change_count"] == 0
    assert report["historical_sensitive_commit_count"] == 2
    assert report["historical_sensitive_change_count"] == 2
    assert report["status"] == "HOLD_PUBLIC_ENV_HISTORY"
    assert report["reads_file_contents"] is False
    assert report["credential_custody_closed"] is False
    assert report["production_release_authorized"] is False


def test_merged_side_branch_add_delete_cannot_hide_sensitive_history(history_repo):
    repo, base = history_repo
    main = _git(repo, "symbolic-ref", "--short", "HEAD")
    _git(repo, "checkout", "-qb", "synthetic-side")
    (repo / ".env.side").write_text("SYNTHETIC_PLACEHOLDER_ONLY\\n")
    _git(repo, "add", "-f", ".env.side")
    _git(repo, "commit", "-qm", "add on side")
    _git(repo, "rm", "-q", ".env.side")
    _git(repo, "commit", "-qm", "remove on side")
    _git(repo, "checkout", "-q", main)
    (repo / "README.md").write_text("harmless updated fixture\\n")
    _git(repo, "commit", "-qam", "independent main change")
    _git(repo, "merge", "--no-ff", "-qm", "merge synthetic side", "synthetic-side")
    head = _git(repo, "rev-parse", "HEAD")

    report = guard.audit(base, head)
    assert report["net_sensitive_change_count"] == 0
    assert report["historical_sensitive_commit_count"] >= 2
    assert report["status"] == "HOLD_PUBLIC_ENV_HISTORY"


def test_unrelated_commit_passes_only_filename_gate_not_custody(history_repo):
    repo, base = history_repo
    (repo / "README.md").write_text("harmless change only\\n")
    _git(repo, "commit", "-qam", "documentation update")
    head = _git(repo, "rev-parse", "HEAD")

    report = guard.audit(base, head)
    assert report["status"] == "PASS_NO_ENV_DIFF"
    assert report["net_sensitive_change_count"] == 0
    assert report["historical_sensitive_change_count"] == 0
    assert report["credential_custody_closed"] is False
    assert report["production_release_authorized"] is False


def test_excessive_commit_history_holds_without_reading_files(monkeypatch):
    base, head = "a" * 40, "b" * 40
    calls = []

    def fake_git(*args):
        calls.append(args)
        if args[0] == "merge-base":
            return (base + "\\n").encode()
        if args[0] == "rev-list":
            return (("c" * 40 + " " + base + "\\n") * (guard.MAX_ANCESTRY_COMMITS + 1)).encode()
        raise AssertionError("must stop before examining unbounded history")

    monkeypatch.setattr(guard, "_git_metadata", fake_git)
    with pytest.raises(ValueError, match="too many commits"):
        guard.audit(base, head)
    assert len(calls) == 2
