from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_final_cutover_evidence",
    ROOT / "scripts" / "validate-final-cutover-evidence.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


SHA = "a" * 64
GIT_SHA = "b" * 40


def complete() -> dict:
    return {
        "schema_version": 1,
        "migration_id": "full-auto-reconciliation-20260730",
        "updated_at": "2026-07-30T22:00:00Z",
        "operator": "operator",
        "production_changed": False,
        "public_inference_found": False,
        "ci": {
            name: {
                "status": "pass",
                "commit_sha": GIT_SHA,
                "workflow_run_id": "123",
                **(
                    {"pull_request": 10, "draft": True}
                    if name == "hermes_agent"
                    else {}
                ),
            }
            for name in ("auto_assist", "auto_router", "hermes_agent")
        },
        "hermes_external_gateway": {
            "status": "pass",
            "config_path": "artifacts/hermes/config.yaml",
            "config_sha256": SHA,
            "mode": "external",
            "default_model": "auto/code",
            "fleet_nodes_present": False,
            "serve_route_disabled": True,
            "discover_route_disabled": True,
            "status_reads_router_admission": True,
            "evidence_path": "artifacts/hermes/evidence.json",
            "evidence_sha256": SHA,
        },
        "runtime_projection": {
            "approval_status": "pass",
            "convergence_status": "pass",
            "generation": 3,
            "revision": "fleet-3",
            "projection_checksum": SHA,
            "approval_manifest_path": "deploy/runtime.yaml",
            "approval_manifest_sha256": SHA,
            "approval_evidence_path": "artifacts/approval.json",
            "approval_evidence_sha256": SHA,
            "convergence_evidence_path": "artifacts/convergence.json",
            "convergence_evidence_sha256": SHA,
            "old_generation_lease_preservation": "pass",
            "evidence_expiry_verified": "pass",
        },
        "executor_containment": {
            "status": "pass",
            "rendered_compose_path": "artifacts/compose.json",
            "rendered_compose_sha256": SHA,
            "evidence_path": "artifacts/containment.json",
            "evidence_sha256": SHA,
            "non_root": True,
            "read_only_root": True,
            "no_ssh_mount": True,
            "no_broad_repo_mount": True,
            "no_docker_socket": True,
            "no_public_credentials": True,
            "no_web_tools": True,
            "one_worktree_maximum": True,
        },
        "image_restore": {
            "status": "pass",
            "image_manifest_path": "artifacts/images/manifest.json",
            "image_manifest_sha256": SHA,
            "bundle_path": "artifacts/images/images.tar",
            "bundle_sha256": SHA,
            "restore_evidence_path": "artifacts/images/restore.json",
            "restore_evidence_sha256": SHA,
            "docker_load_without_pull": True,
            "all_image_ids_present": True,
        },
        "strict_offline_authority": {
            "status": "pass",
            "assistx_public_provider_projection_absent": True,
            "router_nonlocal_lanes_blocked": True,
            "router_tool_job_authority_blocked": True,
            "auto_assign_absent": True,
            "paperclip_inference_path_absent": True,
            "hosted_credentials_absent": True,
            "openapi_evidence_path": "artifacts/openapi.json",
            "openapi_evidence_sha256": SHA,
        },
        "control_room": {
            "status": "pass",
            "human_readable_tasks": True,
            "physical_runtime_identity_visible": True,
            "runtime_kind_version_visible": True,
            "model_quant_context_visible": True,
            "selected_transport_visible": True,
            "tps_ttft_latency_errors_visible": True,
            "projection_generation_visible": True,
            "shared_snapshot_cache_verified": True,
            "evidence_path": "artifacts/control-room.json",
            "evidence_sha256": SHA,
        },
        "blockers": [],
        "notes": [],
    }


def test_complete_contract_passes() -> None:
    assert module.validate(complete()) == []


def test_contract_fails_on_authority_leak_or_missing_evidence() -> None:
    payload = complete()
    payload["hermes_external_gateway"]["fleet_nodes_present"] = True
    payload["runtime_projection"]["convergence_status"] = "not_run"
    payload["image_restore"]["bundle_sha256"] = None
    payload["blockers"] = ["unresolved"]

    errors = module.validate(payload)

    assert any("fleet_nodes_present must be false" in item for item in errors)
    assert any("convergence_status must be pass" in item for item in errors)
    assert any("bundle_sha256 must be SHA-256" in item for item in errors)
    assert any("blockers must be an empty list" in item for item in errors)
