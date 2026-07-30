from __future__ import annotations

import copy
import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts" / "validate-reconciliation-state.py"
REPORT_PATH = ROOT / "scripts" / "render-reconciliation-report.py"
BOOTSTRAP_PATH = ROOT / "scripts" / "bootstrap-reconciliation-worktrees.sh"
EXAMPLE_PATH = ROOT / "deploy" / "reconciliation" / "migration-state.example.yaml"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_validator() -> ModuleType:
    return _load_module("validate_reconciliation_state", VALIDATOR_PATH)


def _load_reporter() -> ModuleType:
    return _load_module("render_reconciliation_report", REPORT_PATH)


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
    for index, name in enumerate(
        ("auto-assist", "auto-router", "hermes-agent", "fleet-llm-profiles", "lms"),
        start=1,
    ):
        data["repositories"][name].update(
            {
                "commit_sha": f"{index:040x}",
                "branch": "full-auto-reconciliation-20260730",
                "clean": True,
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
            "compose_render_assistx": "artifacts/reconciliation-render/assistx-router.yaml",
            "compose_render_router": "../auto-router/artifacts-reconciliation/router-rendered.yaml",
            "compose_checksums_recorded": True,
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


def _complete_cutover_state() -> dict[str, Any]:
    data = _passing_shadow_state()
    data["checks"]["hermes_synthetic_task"] = "pass"
    data["checks"]["neo4j_backup"] = "pass"
    for approval in ("hermes_shadow_executor", "production_backup", "production_cutover"):
        data["approvals"][approval].update(
            {"approved_by": "operator", "approved_at": "2099-01-01T00:00:00Z"}
        )
    data["production_state"]["neo4j"].update(
        {
            "version": "5-test",
            "database": "assistx",
            "backup_method": "test-snapshot",
            "backup_path": "artifacts/backups/neo4j-test",
            "backup_checksum": "sha256:test",
            "backup_created_at": "2099-01-01T00:00:00Z",
            "restore_command_recorded": True,
        }
    )
    data["cutover"].update(
        {
            "recommended": True,
            "client_switch_plan_recorded": True,
            "old_stack_restart_plan_recorded": True,
            "rollback_thresholds_recorded": True,
            "exact_commands_evidence_path": "artifacts/cutover/commands.txt",
        }
    )
    data["rollback"].update(
        {
            "rehearsed": True,
            "exact_commands_evidence_path": "artifacts/rollback/commands.txt",
            "old_stack_health_after_rehearsal": "pass",
        }
    )
    return data


def test_example_state_is_blocked() -> None:
    validator = _load_validator()

    errors = validator.validate(_load_example(), require_cutover=False)

    assert errors
    assert "public_inference_found must be false" in errors
    assert "baseline.captured must be true" in errors
    assert "repositories.auto-assist.commit_sha must be a full 40-character Git SHA" in errors


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
    assert "production_cutover approval is required" in errors
    assert "production_state.neo4j.backup_checksum is required" in errors
    assert "cutover.recommended must be true" in errors


def test_cutover_state_passes_when_all_operator_gates_are_recorded() -> None:
    validator = _load_validator()

    errors = validator.validate(_complete_cutover_state(), require_cutover=True)

    assert errors == []


def test_dirty_or_unpinned_repository_blocks_shadow_readiness() -> None:
    validator = _load_validator()
    data = _passing_shadow_state()
    data["repositories"]["auto-router"]["commit_sha"] = "short"
    data["repositories"]["auto-router"]["clean"] = False

    errors = validator.validate(data, require_cutover=False)

    assert "repositories.auto-router.commit_sha must be a full 40-character Git SHA" in errors
    assert "repositories.auto-router.clean must be true" in errors


def test_report_renderer_includes_checksum_gates_and_runtime(tmp_path: Path) -> None:
    reporter = _load_reporter()
    state_path = tmp_path / "migration-state.yaml"
    state = _passing_shadow_state()
    state_path.write_text(yaml.safe_dump(state), encoding="utf-8")

    report = reporter.render(state, state_path)

    assert "Ledger SHA-256" in report
    assert "RUNTIME_IDENTITY_GATE: pass" in report
    assert "lmstudio:x1-370:1234" in report
    assert "This report is derived from the operator-owned ledger" in report


def test_worktree_bootstrap_script_has_valid_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(BOOTSTRAP_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
