#!/usr/bin/env python3
"""Validate the operator-owned reconciliation migration state ledger."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

import yaml

PASS = "pass"
BRANCH = "full-auto-reconciliation-20260730"
REQUIRED_REPOSITORIES = (
    "auto-assist",
    "auto-router",
    "hermes-agent",
    "fleet-llm-profiles",
    "lms",
)


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("migration state must be a YAML object")
    return data


def _present(value: Any) -> bool:
    return value not in (None, "", [], {}, "unknown", "not_run")


def _get(data: dict[str, Any], dotted: str, default: Any = None) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def _valid_commit_sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{40}", value) is not None


def _repository_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    repositories = data.get("repositories")
    if not isinstance(repositories, dict):
        return ["repositories must be an object"]

    for name in REQUIRED_REPOSITORIES:
        record = repositories.get(name)
        prefix = f"repositories.{name}"
        if not isinstance(record, dict):
            errors.append(f"{prefix} is required")
            continue
        _require(errors, _present(record.get("path")), f"{prefix}.path is required")
        _require(
            errors,
            _valid_commit_sha(record.get("commit_sha")),
            f"{prefix}.commit_sha must be a full 40-character Git SHA",
        )
        _require(errors, record.get("branch") == BRANCH, f"{prefix}.branch must equal {BRANCH}")
        _require(errors, record.get("clean") is True, f"{prefix}.clean must be true")
    return errors


def _runtime_errors(runtime: Any, index: int, *, require_cutover: bool) -> list[str]:
    errors: list[str] = []
    prefix = f"runtimes[{index}]"
    if not isinstance(runtime, dict):
        return [f"{prefix} must be an object"]

    admitted = bool(runtime.get("admitted"))
    if not admitted and not require_cutover:
        return errors
    if require_cutover and runtime.get("disposition") == "production_candidate":
        admitted = True
    if not admitted:
        return errors

    for field in (
        "runtime_node_id",
        "observer_node_id",
        "access_url",
        "transport",
        "runtime_instance_id",
        "runtime_kind",
        "runtime_version",
        "model_instance_id",
        "model_key",
        "artifact_fingerprint",
        "quantization",
        "load_owner",
        "source_kind",
        "benchmark_run_id",
        "observed_at",
        "expires_at",
    ):
        _require(errors, _present(runtime.get(field)), f"{prefix}.{field} is required")

    context_length = runtime.get("context_length")
    _require(
        errors,
        isinstance(context_length, int)
        and not isinstance(context_length, bool)
        and context_length >= 1,
        f"{prefix}.context_length must be an integer >= 1",
    )
    slots = runtime.get("parallel_slots")
    _require(
        errors,
        isinstance(slots, int) and not isinstance(slots, bool) and slots >= 1,
        f"{prefix}.parallel_slots must be an integer >= 1",
    )
    _require(errors, runtime.get("loaded") is True, f"{prefix}.loaded must be true")
    for probe in (
        "completion_probe",
        "sequential_stability_probe",
        "concurrency_probe",
        "cancellation_probe",
    ):
        _require(errors, runtime.get(probe) == PASS, f"{prefix}.{probe} must be pass")
    _require(
        errors,
        not _present(runtime.get("quarantine_reason")),
        f"{prefix} cannot be admitted with a quarantine_reason",
    )

    expires = runtime.get("expires_at")
    if isinstance(expires, str) and expires:
        try:
            parsed = dt.datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            now = dt.datetime.now(tz=dt.timezone.utc)
            _require(errors, parsed > now, f"{prefix}.expires_at is stale")
        except ValueError:
            errors.append(f"{prefix}.expires_at must be ISO-8601")
    return errors


def validate(data: dict[str, Any], *, require_cutover: bool) -> list[str]:
    errors: list[str] = []

    _require(errors, data.get("schema_version") == 1, "schema_version must equal 1")
    _require(errors, _present(data.get("migration_id")), "migration_id is required")
    _require(
        errors,
        data.get("production_changed") is False,
        "production_changed must remain false before cutover",
    )
    _require(
        errors,
        data.get("public_inference_found") is False,
        "public_inference_found must be false",
    )
    errors.extend(_repository_errors(data))

    _require(errors, _get(data, "baseline.captured") is True, "baseline.captured must be true")
    _require(
        errors,
        _present(_get(data, "baseline.evidence_path")),
        "baseline.evidence_path is required",
    )
    _require(
        errors,
        _present(_get(data, "baseline.evidence_sha256_path")),
        "baseline.evidence_sha256_path is required",
    )
    _require(
        errors,
        _get(data, "baseline.production_restart_commands_recorded") is True,
        "production restart commands must be recorded",
    )
    for field in (
        "listening_ports_reviewed",
        "tailscale_snapshot_reviewed",
        "runtime_process_inventory_reviewed",
    ):
        _require(errors, _get(data, f"baseline.{field}") is True, f"baseline.{field} must be true")

    for path in (
        "shadow.isolated_project",
        "shadow.isolated_network",
        "shadow.isolated_neo4j",
        "shadow.isolated_redis",
        "shadow.loopback_only",
        "shadow.compose_checksums_recorded",
    ):
        _require(errors, _get(data, path) is True, f"{path} must be true")
    for path in (
        "shadow.compose_render_assistx",
        "shadow.compose_render_router",
    ):
        _require(errors, _present(_get(data, path)), f"{path} is required")
    _require(errors, _get(data, "shadow.assistx_health") == PASS, "shadow AssistX health must pass")
    _require(errors, _get(data, "shadow.router_health") == PASS, "shadow router health must pass")

    critical_checks = [
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
    ]
    if require_cutover:
        critical_checks.extend(("hermes_synthetic_task", "neo4j_backup"))
    for name in critical_checks:
        _require(errors, _get(data, f"checks.{name}") == PASS, f"checks.{name} must be pass")

    runtimes = data.get("runtimes", [])
    _require(errors, isinstance(runtimes, list), "runtimes must be a list")
    if isinstance(runtimes, list):
        for index, runtime in enumerate(runtimes):
            errors.extend(_runtime_errors(runtime, index, require_cutover=require_cutover))
        _require(
            errors,
            any(isinstance(runtime, dict) and runtime.get("admitted") is True for runtime in runtimes),
            "at least one runtime must be admitted",
        )

    blockers = data.get("blockers", [])
    _require(errors, isinstance(blockers, list) and not blockers, "blockers must be an empty list")

    if require_cutover:
        for approval in (
            "hermes_shadow_executor",
            "production_backup",
            "production_cutover",
        ):
            _require(
                errors,
                _present(_get(data, f"approvals.{approval}.approved_by")),
                f"{approval} approval is required",
            )
            _require(
                errors,
                _present(_get(data, f"approvals.{approval}.approved_at")),
                f"{approval} approval timestamp is required",
            )

        for path in (
            "production_state.neo4j.version",
            "production_state.neo4j.database",
            "production_state.neo4j.backup_method",
            "production_state.neo4j.backup_path",
            "production_state.neo4j.backup_checksum",
            "production_state.neo4j.backup_created_at",
        ):
            _require(errors, _present(_get(data, path)), f"{path} is required")
        _require(
            errors,
            _get(data, "production_state.neo4j.restore_command_recorded") is True,
            "production Neo4j restore command must be recorded",
        )
        _require(errors, _get(data, "cutover.recommended") is True, "cutover.recommended must be true")
        for path, message in (
            ("cutover.client_switch_plan_recorded", "client switch plan must be recorded"),
            ("cutover.old_stack_restart_plan_recorded", "old-stack restart plan must be recorded"),
            ("cutover.rollback_thresholds_recorded", "rollback thresholds must be recorded"),
        ):
            _require(errors, _get(data, path) is True, message)
        _require(
            errors,
            _present(_get(data, "cutover.exact_commands_evidence_path")),
            "cutover exact command evidence path is required",
        )
        _require(errors, _get(data, "rollback.rehearsed") is True, "rollback must be rehearsed")
        _require(
            errors,
            _present(_get(data, "rollback.exact_commands_evidence_path")),
            "rollback exact command evidence path is required",
        )
        _require(
            errors,
            _get(data, "rollback.old_stack_health_after_rehearsal") == PASS,
            "old-stack health after rollback rehearsal must pass",
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("state_file", nargs="?", default="deploy/reconciliation/migration-state.yaml")
    parser.add_argument("--require-cutover", action="store_true")
    args = parser.parse_args()

    path = Path(args.state_file)
    if not path.exists():
        print(f"BLOCKED: migration state file not found: {path}", file=sys.stderr)
        return 2

    try:
        data = _load(path)
    except Exception as exc:
        print(f"BLOCKED: cannot read migration state: {exc}", file=sys.stderr)
        return 2

    errors = validate(data, require_cutover=args.require_cutover)
    if errors:
        print("BLOCKED: reconciliation state did not satisfy required gates")
        for error in errors:
            print(f"- {error}")
        return 1

    mode = "production cutover" if args.require_cutover else "shadow readiness"
    print(f"PASS: reconciliation state satisfies {mode} gates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
