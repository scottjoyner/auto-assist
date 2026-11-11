"""Zero-network regression: fresh activity must not disguise stale free models."""
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from free_pool_status import build_status


def fixture(tmp_path):
    now = datetime.now(UTC)
    projection = {
        "schema": "free-pool-projection/v1",
        "read_only": True,
        "authoritative_routing_unchanged": True,
        "zai_policy": "reserve_only",
        "kilo": {"models": [{"id": "synthetic/exact:free",
                             "recommendation_tier": "proven"}]},
    }
    path = tmp_path / "free-pool-projection.json"
    path.write_text(json.dumps(projection))
    refresh = {
        "schema": "free-pool-refresh-run/v1", "executed": True,
        "paid_fallback": False, "finished_at": now.isoformat(),
    }
    receipt = tmp_path / "last-free-pool-refresh.json"
    receipt.write_text(json.dumps(refresh))
    (tmp_path / "live.jsonl").write_text(json.dumps({
        "status": "completed", "session": "synthetic-recent",
        "provider": "kilo_free", "model": "synthetic/exact:free",
        "updated_at": now.isoformat(),
    }) + "\n")
    (tmp_path / "history.jsonl").write_text("")
    (tmp_path / "provider-qualification.jsonl").write_text("")
    return path, receipt, now


def get(tmp_path):
    result = build_status(state_root=tmp_path, process_alive=lambda _: False)
    assert result["dispatch_allowed"] is False
    assert result["account_entitlement_proven"] is False
    assert result["shared_quota_lease_proven"] is False
    return result


def test_recent_local_evidence_is_not_provider_entitlement(tmp_path):
    fixture(tmp_path)
    result = get(tmp_path)
    assert result["lifecycle_recent"] is True
    assert result["projection_fresh"] is True
    assert result["evidence_fresh"] is True
    assert result["models"]["proven"] == ["synthetic/exact:free"]


def test_stale_projection_denied_despite_fresh_session(tmp_path):
    projection, _, now = fixture(tmp_path)
    old = (now - timedelta(days=34)).timestamp()
    os.utime(projection, (old, old))
    result = get(tmp_path)
    assert result["lifecycle_recent"] is True
    assert result["projection_fresh"] is False
    assert result["evidence_fresh"] is False
    assert "projection_missing_or_stale" in result["projection_blockers"]
    assert result["models"]["proven"] == []
    assert result["historical_model_counts"]["proven"] == 1


def test_touching_old_refresh_does_not_make_it_fresh(tmp_path):
    _, receipt, now = fixture(tmp_path)
    data = json.loads(receipt.read_text())
    data["finished_at"] = (now - timedelta(days=34)).isoformat()
    receipt.write_text(json.dumps(data))
    result = get(tmp_path)
    assert "refresh_completion_stale" in result["projection_blockers"]
    assert result["models"]["proven"] == []


def test_missing_refresh_fails_closed(tmp_path):
    _, receipt, _ = fixture(tmp_path)
    receipt.unlink()
    result = get(tmp_path)
    assert "refresh_missing_or_stale" in result["projection_blockers"]
    assert result["models"]["trial"] == []


def test_malformed_refresh_fails_closed(tmp_path):
    _, receipt, _ = fixture(tmp_path)
    receipt.write_text("not-json")
    result = get(tmp_path)
    assert "refresh_invalid" in result["projection_blockers"]


def test_symlinked_projection_fails_closed(tmp_path):
    projection, _, _ = fixture(tmp_path)
    source = tmp_path / "original-projection.json"
    projection.rename(source)
    projection.symlink_to(source)
    result = get(tmp_path)
    assert "projection_missing_or_stale" in result["projection_blockers"]
def test_unqualified_projection_schema_fails_closed(tmp_path):
    projection, _, _ = fixture(tmp_path)
    data = json.loads(projection.read_text())
    data["read_only"] = False
    projection.write_text(json.dumps(data))
    result = get(tmp_path)
    assert "projection_contract_missing" in result["projection_blockers"]


def test_future_session_does_not_claim_freshness(tmp_path):
    fixture(tmp_path)
    future = datetime.now(UTC) + timedelta(days=1)
    (tmp_path / "live.jsonl").write_text(json.dumps({
        "status": "completed", "session": "future",
        "provider": "kilo_free", "model": "synthetic/exact:free",
        "updated_at": future.isoformat(),
    }) + "\n")
    result = get(tmp_path)
    assert result["projection_fresh"] is True
    assert result["lifecycle_recent"] is False
    assert result["evidence_fresh"] is False
