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
