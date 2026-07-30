#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

import yaml

_ALLOWED_STATUSES = {
    "healthy",
    "degraded",
    "disabled",
    "unhealthy",
    "unknown",
    "not_applicable",
}
_PLACEHOLDERS = {"", "change-me", "unknown", "todo", "tbd", "none"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def validate_dependency_registry(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if document.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    policy = document.get("policy") or {}
    expected_policy = {
        "inference_egress": "private_only",
        "public_inference": "forbidden",
        "unknown_required_dependency": "blocks_cutover",
        "missing_required_evidence": "blocks_cutover",
        "image_restore_without_internet": "required",
        "executor_mounts_default": "deny",
    }
    for key, expected in expected_policy.items():
        if policy.get(key) != expected:
            errors.append(f"policy.{key} must be {expected!r}")

    dependencies = document.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        return [*errors, "dependencies must be a non-empty list"]

    declared_required = {_text(value) for value in document.get("required_dependency_ids") or []}
    seen: set[str] = set()
    found_required: set[str] = set()

    for index, dependency in enumerate(dependencies):
        prefix = f"dependencies[{index}]"
        if not isinstance(dependency, dict):
            errors.append(f"{prefix} must be a mapping")
            continue
        dependency_id = _text(dependency.get("id"))
        if not dependency_id:
            errors.append(f"{prefix}.id is required")
            continue
        if dependency_id in seen:
            errors.append(f"duplicate dependency id: {dependency_id}")
        seen.add(dependency_id)

        required = dependency.get("required") is True
        if required:
            found_required.add(dependency_id)
        status = _text(dependency.get("status")).lower()
        expected_state = _text(dependency.get("expected_state")).lower()
        if status not in _ALLOWED_STATUSES:
            errors.append(f"{dependency_id}.status is invalid: {status or '<empty>'}")
        if expected_state not in {"healthy", "disabled", "not_applicable"}:
            errors.append(f"{dependency_id}.expected_state must be healthy, disabled, or not_applicable")

        if required:
            if expected_state == "disabled" and status != "disabled":
                errors.append(f"{dependency_id} must be disabled before cutover (currently {status})")
            elif expected_state == "healthy" and status != "healthy":
                errors.append(f"{dependency_id} must be healthy before cutover (currently {status})")
            elif expected_state == "not_applicable" and status != "not_applicable":
                errors.append(f"{dependency_id} must be explicitly not_applicable (currently {status})")

            evidence = dependency.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"{dependency_id}.evidence must contain current machine-side evidence")
            else:
                normalized = [_text(item).lower() for item in evidence]
                if any(item in _PLACEHOLDERS for item in normalized):
                    errors.append(f"{dependency_id}.evidence contains a placeholder")

        for field in ("owner", "failure_impact", "probe", "rollback"):
            if not _text(dependency.get(field)):
                errors.append(f"{dependency_id}.{field} is required")

    missing_required = sorted(declared_required - found_required)
    undeclared_required = sorted(found_required - declared_required)
    if missing_required:
        errors.append(f"required_dependency_ids missing entries in dependencies: {', '.join(missing_required)}")
    if undeclared_required:
        errors.append(f"required dependencies missing from required_dependency_ids: {', '.join(undeclared_required)}")

    registry_status = _text(document.get("status")).lower()
    if not errors and registry_status != "ready":
        errors.append("status must be 'ready' after all dependency gates pass")
    return errors


def load_registry(path: pathlib.Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("dependency registry root must be a mapping")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the external dependency cutover registry")
    parser.add_argument("registry", type=pathlib.Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    if not args.registry.exists():
        message = f"dependency registry does not exist: {args.registry}"
        if args.as_json:
            print(json.dumps({"ok": False, "errors": [message]}))
        else:
            print(f"BLOCKED: {message}", file=sys.stderr)
        return 2

    try:
        document = load_registry(args.registry)
    except Exception as exc:
        message = f"failed to read dependency registry: {exc}"
        if args.as_json:
            print(json.dumps({"ok": False, "errors": [message]}))
        else:
            print(f"BLOCKED: {message}", file=sys.stderr)
        return 2

    errors = validate_dependency_registry(document)
    result = {
        "ok": not errors,
        "registry": str(args.registry),
        "status": document.get("status"),
        "dependency_count": len(document.get("dependencies") or []),
        "errors": errors,
    }
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif errors:
        print("External dependency gate: BLOCKED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
    else:
        print("External dependency gate: PASS")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
