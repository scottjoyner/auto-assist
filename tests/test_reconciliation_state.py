from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts" / "validate-reconciliation-state.py"
EXAMPLE_PATH = ROOT / "deploy" / "reconciliation" / "migration-state.example.yaml"


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_reconciliation_state", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_example() -> dict[str, Any]:
    data = yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _passing_shadow_state() -> dict[str, Any]:
    data = copy.deepcopy(_load_example())
    data.update(
        {
            "status": "shadow_validated",
            "updated_at": "2099-01-01T00:00:00Z",
            "public_inference_found": False,
        }
    )
    data["baseline"].update(
        {
            "captured": True,
            "evidence_path": "artifacts/reconciliation-preflight/example",
            "evidence_sha256_path": "artifacts/reconciliation-preflight/example/SHA256SUMS",
            "production_restart_commands_recorded": True,
            "listening_ports_reviewed": True,
            "tailscale_snapshot_reviewed": True,
            "runtime_process_inventory_reviewed": True,
        }
    )
    data["shadow"].update(
        {
            "isolated_project": True,
            "isolated_network": True,
            "isolated_neo4j": True,
            "isolated_redis": True,
            "loopback_only": True,
            "assistx_health": "pass",
            "router_health": "pass",
        }
    )
    for check in (
        "assistx_ci",
        "router_ci",
        "compose_render",
        "strict_offline_verifier",
        "direct_completion",
        "routed_completion",
        "runtime_identity",
        "slot_capacity",
        "cancellation_safety",
        "state_authority",
        "restart_rebuild",
        "rollback_rehearsal",
    ):
        data["checks"][check] = "pass"

    data["runtimes"] = [
        {
            "runtime_node_id": "x1-370",
            "observer_node_id": "x1-370",
            "access_url": "http://host.docker.internal:1234/v1",
            "transport": "direct",
            "runtime_instance_id": "lmstudio:x1-370:1234",
            "runtime_kind": "lmstudio",
            "runtime_version": "test",
            "model_instance_id": "lmstudio:x1-370:1234:model:test",
            "model_key": "test-model",
            "artifact_fingerprint": "sha256:test",
            "quantization": "test",
            "context_length": 32768,
            "loaded": True,
            "load_owner": "operator",
            "parallel_slots": 1,
            "active_requests_observed": 0,
            "completion_probe": "pass",
            "sequential_stability_probe": "pass",
            "concurrency_probe": "pass",
            "cancellation_probe": "pass",
            "benchmark_run_id": "test-run",
            "observed_at": "2098-12-31T23:00:00Z",
            "expires_at": "2099-01-01T01:00:00Z",
            "source_kind": "official_lms_ps",
            "disposition": "shadow_candidate",
            "admitted": True,
            "quarantine_reason": None,
        }
    ]
    data["blockers"] = []
    return data


def test_example_state_is_blocked() -> None:
    validator = _load_validator()

    errors = validator.validate(_load_example(), require_cutover=False)

    assert errors
    assert "public_inference_found must be false" in errors
    assert "baseline.captured must be true" in errors


def test_complete_shadow_state_passes() -> None:
    validator = _load_validator()

    errors = validator.validate(_passing_shadow_state(), require_cutover=False)

    assert errors == []


def test_cutover_requires_approval_backup_and_hermes_gate() -> None:
    validator = _load_validator()
    data = _passing_shadow_state()

    errors = validator.validate(data, require_cutover=True)

    assert "checks.hermes_synthetic_task must be pass" in errors
    assert "checks.neo4j_backup must be pass" in errors
    assert "production cutover approval is required" in errors
    assert "cutover.recommended must be true" in errors


def test_cutover_state_passes_when_all_operator_gates_are_recorded() -> None:
    validator = _load_validator()
    data = _passing_shadow_state()
    data["checks"]["hermes_synthetic_task"] = "pass"
    data["checks"]["neo4j_backup"] = "pass"
    data["approvals"]["production_cutover"].update(
        {"approved_by": "operator", "approved_at": "2099-01-01T00:00:00Z"}
    )
    data["cutover"].update(
        {
            "recommended": True,
            "client_switch_plan_recorded": True,
            "old_stack_restart_plan_recorded": True,
            "rollback_thresholds_recorded": True,
        }
    )
    data["rollback"]["rehearsed"] = True

    errors = validator.validate(data, require_cutover=True)

    assert errors == []
