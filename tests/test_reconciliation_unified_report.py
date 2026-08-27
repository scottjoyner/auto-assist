from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reconciliation-unified-report.py"
SPEC = importlib.util.spec_from_file_location("reconciliation_unified_report", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_build_report_joins_sources_without_admitting_candidates(tmp_path: Path) -> None:
    ledger_path = tmp_path / "migration-state.yaml"
    ledger_path.write_text(
        yaml.safe_dump(
            {
                "migration_id": "test-migration",
                "status": "shadow_validated",
                "production_changed": False,
                "runtimes": [
                    {"runtime_node_id": "xwing", "admitted": True},
                    {"runtime_node_id": "dead", "admitted": False, "quarantine_reason": "offline"},
                ],
                "blockers": ["operator approval pending"],
            }
        ),
        encoding="utf-8",
    )
    candidates_path = tmp_path / "candidates.json"
    candidates_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "authority": "candidate_reachability_only",
                "nodes": [
                    {"node_id": "xwing", "online": True, "admission_status": "candidate_only"},
                    {"node_id": "dead", "online": False, "admission_status": "candidate_only"},
                ],
            }
        ),
        encoding="utf-8",
    )

    report = MODULE.build_report(
        ledger_path,
        candidates_path=candidates_path,
        captured_at="2026-01-01T00:00:00+00:00",
    )

    assert report["authority"]["does_not_authorize_production_changes"] is True
    assert report["summary"] == {
        "ledger_status": "shadow_validated",
        "production_changed": False,
        "candidate_nodes": 2,
        "online_candidate_nodes": 1,
        "admitted_runtimes": 1,
        "quarantined_runtimes": 1,
        "blockers": 1,
        "tailnet_is_reachability_only": True,
    }
    assert report["sources"]["ledger"]["sha256"]
    assert report["sources"]["candidate_inventory"]["sha256"]
    assert report["candidate_inventory"]["nodes"][0]["admission_status"] == "candidate_only"


def test_build_report_fails_closed_on_invalid_candidates(tmp_path: Path) -> None:
    ledger_path = tmp_path / "migration-state.yaml"
    ledger_path.write_text(yaml.safe_dump({"runtimes": [], "blockers": []}), encoding="utf-8")
    candidates_path = tmp_path / "candidates.json"
    candidates_path.write_text(json.dumps({"nodes": "not-a-list"}), encoding="utf-8")

    try:
        MODULE.build_report(ledger_path, candidates_path=candidates_path)
    except ValueError as exc:
        assert "nodes must be a list" in str(exc)
    else:
        raise AssertionError("invalid candidate inventory must fail closed")
