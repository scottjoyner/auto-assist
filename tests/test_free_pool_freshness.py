"""Offline regression for current-main free-pool stale-evidence audit."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import free_pool_freshness as audit
import free_subagent_supervisor as supervisor

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
PROJECTION = {
    "schema": "free-pool-projection/v1",
    "read_only": True,
    "authoritative_routing_unchanged": True,
    "kilo": {"models": [{"id": "fictional/never-authorized", "recommendation_tier": "proven"}]},
}
REFRESH = {
    "schema": "free-pool-refresh-run/v1",
    "executed": True,
    "paid_fallback": False,
    "finished_at": NOW.isoformat(),
}


def fixture(tmp_path, *, projection=None, refresh=None, modified=None):
    p, r = tmp_path / "free-pool-projection.json", tmp_path / "last-free-pool-refresh.json"
    p.write_text(json.dumps(PROJECTION if projection is None else projection))
    r.write_text(json.dumps(REFRESH if refresh is None else refresh))
    ts = (modified or NOW).timestamp()
    os.utime(p, (ts, ts))
    os.utime(r, (ts, ts))
    return p, r


def check(root):
    result = audit.review_state(root, now=NOW)
    assert result["decision"] == "HOLD"
    assert result["dispatch_allowed"] is False
    assert result["account_entitlement_proven"] is False
    assert result["provider_billing_verified"] is False
    assert result["shared_lease_enforced"] is False
    assert result["inference_calls"] == 0
    assert result["provider_requests"] == 0
    assert "fictional/never-authorized" not in json.dumps(result)
    return result


def test_recent_local_files_still_cannot_grant_route(tmp_path):
    fixture(tmp_path)
    report = check(tmp_path)
    assert report["local_freshness"] == "recent_untrusted_local_evidence"
    assert report["blockers"] == ["account_and_upstream_authority_unverified"]


def test_old_local_files_fail_closed(tmp_path):
    fixture(tmp_path, modified=NOW - timedelta(days=34))
    report = check(tmp_path)
    assert "projection_file_stale_or_future" in report["blockers"]
    assert "refresh_file_stale_or_future" in report["blockers"]


def test_touching_old_completion_record_cannot_renew(tmp_path):
    fixture(tmp_path, refresh={**REFRESH, "finished_at": (NOW - timedelta(days=34)).isoformat()})
    assert "refresh_completion_stale_or_invalid" in check(tmp_path)["blockers"]


@pytest.mark.parametrize("filename,reason", [
    ("free-pool-projection.json", "projection_unreadable_or_invalid"),
    ("last-free-pool-refresh.json", "refresh_unreadable_or_invalid"),
])
def test_missing_or_symlinked_files_fail_closed(tmp_path, filename, reason):
    p, r = fixture(tmp_path)
    (tmp_path / filename).unlink()
    assert reason in check(tmp_path)["blockers"]
    (tmp_path / filename).symlink_to(p if filename != p.name else r)
    assert reason in check(tmp_path)["blockers"]


@pytest.mark.parametrize("patch", [
    {"read_only": False},
    {"authoritative_routing_unchanged": False},
    {"schema": "bogus"},
])
def test_projection_contract_rejected(tmp_path, patch):
    fixture(tmp_path, projection={**PROJECTION, **patch})
    assert "projection_contract_invalid" in check(tmp_path)["blockers"]


@pytest.mark.parametrize("patch", [
    {"executed": False},
    {"paid_fallback": True},
    {"schema": "bogus"},
])
def test_refresh_contract_rejected(tmp_path, patch):
    fixture(tmp_path, refresh={**REFRESH, **patch})
    assert "refresh_contract_invalid" in check(tmp_path)["blockers"]


@pytest.mark.parametrize("stamp", [
    "2026-11-11T12:00:00",
    "garbage",
    "2026-11-11T13:00:00+00:00",
    "2026-10-07T12:00:00+00:00",
])
def test_bad_refresh_completion_denied(tmp_path, stamp):
    fixture(tmp_path, refresh={**REFRESH, "finished_at": stamp})
    assert "refresh_completion_stale_or_invalid" in check(tmp_path)["blockers"]


def test_future_file_times_denied(tmp_path):
    fixture(tmp_path, modified=NOW + timedelta(minutes=2))
    report = check(tmp_path)
    assert "projection_file_stale_or_future" in report["blockers"]


@pytest.mark.parametrize("bad", ['["not an object"]', "{bad json", " " * (2*1024*1024+1)])
def test_untrusted_json_is_not_exposed(tmp_path, bad):
    p, _ = fixture(tmp_path)
    p.write_text(bad)
    assert "projection_unreadable_or_invalid" in check(tmp_path)["blockers"]


def test_current_main_supervisor_cli_never_enumerates_or_dispatches(tmp_path, monkeypatch, capsys):
    fixture(tmp_path)
    monkeypatch.setattr(supervisor, "enumerate_free_models",
                        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("provider enumeration forbidden")))
    status = supervisor.main(["--free-pool-freshness", "--free-pool-state-root", str(tmp_path)])
    assert status == 0
    output = json.loads(capsys.readouterr().out)
    assert output["decision"] == "HOLD"
    assert output["dispatch_allowed"] is False
    assert output["provider_requests"] == 0


def test_existing_active_supervision_denial_retained(monkeypatch, capsys):
    monkeypatch.setattr(supervisor, "enumerate_free_models",
                        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must not enumerate")))
    assert supervisor.main(["--supervise", "--no-dry-run"]) == 3
    assert "active_supervision_denied_without_resource_owned_authority" in capsys.readouterr().err


def test_audit_cannot_override_active_mode_denial(tmp_path, capsys):
    fixture(tmp_path)
    assert supervisor.main(["--free-pool-freshness", "--supervise", "--no-dry-run",
                            "--free-pool-state-root", str(tmp_path)]) == 3
    assert "free_pool_audit_conflicts_with_active_mode" in capsys.readouterr().err


def test_audit_does_not_mutate_fixture_files(tmp_path, capsys):
    p, r = fixture(tmp_path)
    before = [(x.read_bytes(), x.stat().st_mtime_ns) for x in (p, r)]
    assert supervisor.main(["--free-pool-freshness", "--free-pool-state-root", str(tmp_path)]) == 0
    assert [(x.read_bytes(), x.stat().st_mtime_ns) for x in (p, r)] == before


def test_fresh_unrelated_session_does_not_renew_old_free_pool(tmp_path):
    fixture(tmp_path, modified=NOW - timedelta(days=34))
    (tmp_path / "live.jsonl").write_text(json.dumps({
        "session": "synthetic-fresh-session", "updated_at": NOW.isoformat(),
        "status": "completed", "model": "fictional/never-authorized",
    }) + "\n")
    report = check(tmp_path)
    assert report["local_freshness"] == "stale_or_unknown"
    assert "projection_file_stale_or_future" in report["blockers"]
