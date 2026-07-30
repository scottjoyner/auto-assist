#!/usr/bin/env python3
"""Validate the operator-owned reconciliation migration state ledger."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

import yaml

PASS = "pass"


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
        "model_instance_id",
        "model_key",
        "load_owner",
        "source_kind",
        "observed_at",
        "expires_at",
    ):
        _require(errors, _present(runtime.get(field)), f"{prefix}.{field} is required")

    slots = runtime.get("parallel_slots")
    _require(
        errors,
        isinstance(slots, int) and not isinstance(slots, bool) and slots >= 1,
        f"{prefix}.parallel_slots must be an integer >= 1",
    )
    _require(errors, runtime.get("loaded") is True, f"{prefix}.loaded must be true")
    _require(
        errors,
        runtime.get("completion_probe") == PASS,
        f"{prefix}.completion_probe must be pass",
    )
    _require(
        errors,
        runtime.get("sequential_stability_probe") == PASS,
        f"{prefix}.sequential_stability_probe must be pass",
    )
    _require(
        errors,
        runtime.get("concurrency_probe") == PASS,
        f"{prefix}.concurrency_probe must be pass",
    )
    _require(
        errors,
        runtime.get("cancellation_probe") == PASS,
        f"{prefix}.cancellation_probe must be pass",
    )
    _require(
        errors,
        not _present(runtime.get("quarantine_reason")),
        f"{prefix} cannot be admitted with a quarantine_reason",
    )

    expires = runtime.get("expires_at")
    if isinstance(expires, str) and expires:
        try:
            parsed = dt.datetime.fromisoformat(expires.replace("Z", "+00:00"))
            now = dt.datetime.now(tz=dt.timezone.utc)
            _require(errors, parsed > now, f"{prefix}.expires_at is stale")
        except ValueError:
            errors.append(f"{prefix}.expires_at must be ISO-8601")
    return errors


def validate(data: dict[str, Any], *, require_cutover: bool) -> list[str]:
    errors: list[str] = []

    _require(errors, data.get("schema_version") == 1, "schema_version must equal 1")
    _require(errors, _present(data.get("migration_id")), "migration_id is required")
    _require(errors, data.get("production_changed") is False, "production_changed must remain false before cutover")
    _require(errors, data.get("public_inference_found") is False, "public_inference_found must be false")

    _require(errors, _get(data, "baseline.captured") is True, "baseline.captured must be true")
    _require(errors, _present(_get(data, "baseline.evidence_path")), "baseline.evidence_path is required")
    _require(
        errors,
        _get(data, "baseline.production_restart_commands_recorded") is True,
        "production restart commands must be recorded",
    )

    for path in (
        "shadow.isolated_project",
        "shadow.isolated_network",
        "shadow.isolated_neo4j",
        "shadow.isolated_redis",
        "shadow.loopback_only",
    ):
        _require(errors, _get(data, path) is True, f"{path} must be true")
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
        _require(
            errors,
            _get(data, "approvals.production_cutover.approved_by") not in (None, ""),
            "production cutover approval is required",
        )
        _require(errors, _get(data, "cutover.recommended") is True, "cutover.recommended must be true")
        _require(
            errors,
            _get(data, "cutover.client_switch_plan_recorded") is True,
            "client switch plan must be recorded",
        )
        _require(
            errors,
            _get(data, "cutover.old_stack_restart_plan_recorded") is True,
            "old-stack restart plan must be recorded",
        )
        _require(
            errors,
            _get(data, "cutover.rollback_thresholds_recorded") is True,
            "rollback thresholds must be recorded",
        )
        _require(errors, _get(data, "rollback.rehearsed") is True, "rollback must be rehearsed")

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
