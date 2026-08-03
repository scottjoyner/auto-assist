#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml


PASS = "pass"
_TRUE_FIELDS = {
    "ci.hermes_agent.external_mode_synchronized": True,
    "hermes_external_gateway.fleet_nodes_present": False,
    "hermes_external_gateway.serve_route_disabled": True,
    "hermes_external_gateway.discover_route_disabled": True,
    "hermes_external_gateway.status_reads_router_admission": True,
    "executor_containment.non_root": True,
    "executor_containment.read_only_root": True,
    "executor_containment.no_ssh_mount": True,
    "executor_containment.no_broad_repo_mount": True,
    "executor_containment.no_docker_socket": True,
    "executor_containment.no_public_credentials": True,
    "executor_containment.no_web_tools": True,
    "executor_containment.one_worktree_maximum": True,
    "image_restore.docker_load_without_pull": True,
    "image_restore.all_image_ids_present": True,
    "strict_offline_authority.assistx_public_provider_projection_absent": True,
    "strict_offline_authority.router_nonlocal_lanes_blocked": True,
    "strict_offline_authority.router_tool_job_authority_blocked": True,
    "strict_offline_authority.auto_assign_absent": True,
    "strict_offline_authority.paperclip_inference_path_absent": True,
    "strict_offline_authority.hosted_credentials_absent": True,
    "control_room.human_readable_tasks": True,
    "control_room.physical_runtime_identity_visible": True,
    "control_room.runtime_kind_version_visible": True,
    "control_room.model_quant_context_visible": True,
    "control_room.selected_transport_visible": True,
    "control_room.tps_ttft_latency_errors_visible": True,
    "control_room.projection_generation_visible": True,
    "control_room.shared_snapshot_cache_verified": True,
}
_STATUS_FIELDS = (
    "ci.auto_assist.status",
    "ci.auto_router.status",
    "ci.hermes_agent.status",
    "hermes_external_gateway.status",
    "runtime_projection.approval_status",
    "runtime_projection.convergence_status",
    "runtime_projection.old_generation_lease_preservation",
    "runtime_projection.evidence_expiry_verified",
    "executor_containment.status",
    "image_restore.status",
    "strict_offline_authority.status",
    "control_room.status",
)
_PATH_AND_HASH_FIELDS = (
    (
        "hermes_external_gateway.config_path",
        "hermes_external_gateway.config_sha256",
    ),
    (
        "hermes_external_gateway.evidence_path",
        "hermes_external_gateway.evidence_sha256",
    ),
    (
        "runtime_projection.approval_manifest_path",
        "runtime_projection.approval_manifest_sha256",
    ),
    (
        "runtime_projection.approval_evidence_path",
        "runtime_projection.approval_evidence_sha256",
    ),
    (
        "runtime_projection.convergence_evidence_path",
        "runtime_projection.convergence_evidence_sha256",
    ),
    (
        "executor_containment.rendered_compose_path",
        "executor_containment.rendered_compose_sha256",
    ),
    (
        "executor_containment.evidence_path",
        "executor_containment.evidence_sha256",
    ),
    (
        "image_restore.image_manifest_path",
        "image_restore.image_manifest_sha256",
    ),
    (
        "image_restore.bundle_path",
        "image_restore.bundle_sha256",
    ),
    (
        "image_restore.restore_evidence_path",
        "image_restore.restore_evidence_sha256",
    ),
    (
        "strict_offline_authority.openapi_evidence_path",
        "strict_offline_authority.openapi_evidence_sha256",
    ),
    (
        "control_room.evidence_path",
        "control_room.evidence_sha256",
    ),
)


def _get(data: dict[str, Any], dotted: str) -> Any:
    value: Any = data
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _present(value: Any) -> bool:
    return value not in (None, "", [], {}, "unknown", "not_run")


def _sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    )


def _git_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{40}", value) is not None
    )


def validate(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("schema_version must equal 1")
    if not _present(data.get("migration_id")):
        errors.append("migration_id is required")
    if not _present(data.get("updated_at")):
        errors.append("updated_at is required")
    if not _present(data.get("operator")):
        errors.append("operator is required")
    if data.get("production_changed") is not False:
        errors.append(
            "production_changed must remain false before authorized cutover"
        )
    if data.get("public_inference_found") is not False:
        errors.append("public_inference_found must be false")

    blockers = data.get("blockers")
    if not isinstance(blockers, list) or blockers:
        errors.append("blockers must be an empty list")

    for field in _STATUS_FIELDS:
        if _get(data, field) != PASS:
            errors.append(f"{field} must be pass")

    for field, expected in _TRUE_FIELDS.items():
        if _get(data, field) is not expected:
            errors.append(f"{field} must be {str(expected).lower()}")

    if _get(data, "hermes_external_gateway.mode") != "external":
        errors.append("hermes_external_gateway.mode must be external")
    default_model = _get(data, "hermes_external_gateway.default_model")
    if not isinstance(default_model, str) or not default_model.startswith("auto/"):
        errors.append(
            "hermes_external_gateway.default_model must be an auto/* alias"
        )

    if _get(data, "ci.hermes_agent.deployment_pull_request") != 11:
        errors.append("ci.hermes_agent.deployment_pull_request must be 11")
    if _get(data, "ci.hermes_agent.source_pull_request") != 10:
        errors.append("ci.hermes_agent.source_pull_request must be 10")
    if not _git_sha(_get(data, "ci.hermes_agent.source_commit_sha")):
        errors.append(
            "ci.hermes_agent.source_commit_sha must be the full tested PR #10 SHA"
        )
    if _get(data, "ci.hermes_agent.draft") is not True:
        errors.append(
            "Hermes source and deployment PRs must remain draft until final review"
        )

    for repository in ("auto_assist", "auto_router", "hermes_agent"):
        sha = _get(data, f"ci.{repository}.commit_sha")
        if not _git_sha(sha):
            errors.append(f"ci.{repository}.commit_sha must be a full Git SHA")
        if not _present(_get(data, f"ci.{repository}.workflow_run_id")):
            errors.append(f"ci.{repository}.workflow_run_id is required")

    generation = _get(data, "runtime_projection.generation")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation <= 0
    ):
        errors.append("runtime_projection.generation must be a positive integer")
    if not _present(_get(data, "runtime_projection.revision")):
        errors.append("runtime_projection.revision is required")
    if not _sha256(_get(data, "runtime_projection.projection_checksum")):
        errors.append("runtime_projection.projection_checksum must be SHA-256")

    for path_field, hash_field in _PATH_AND_HASH_FIELDS:
        if not _present(_get(data, path_field)):
            errors.append(f"{path_field} is required")
        if not _sha256(_get(data, hash_field)):
            errors.append(f"{hash_field} must be SHA-256")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the final airtight cutover evidence contract."
    )
    parser.add_argument(
        "evidence_file",
        nargs="?",
        default="deploy/reconciliation/final-cutover-evidence.yaml",
    )
    args = parser.parse_args()
    path = Path(args.evidence_file)
    if not path.exists():
        print(f"FINAL_CUTOVER_EVIDENCE: BLOCKED missing {path}", file=sys.stderr)
        return 2
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"FINAL_CUTOVER_EVIDENCE: BLOCKED {exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print(
            "FINAL_CUTOVER_EVIDENCE: BLOCKED root must be a mapping",
            file=sys.stderr,
        )
        return 2
    errors = validate(payload)
    if errors:
        print("FINAL_CUTOVER_EVIDENCE: BLOCKED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("FINAL_CUTOVER_EVIDENCE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
