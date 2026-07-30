#!/usr/bin/env python3
"""Render a concise, non-secret migration report from the reconciliation ledger."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

import yaml


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("migration state must be a YAML object")
    return data


def _get(data: dict[str, Any], dotted: str, default: Any = None) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _yes_no(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _gate(value: Any) -> str:
    return str(value or "not_run")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render(data: dict[str, Any], path: Path) -> str:
    checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
    runtimes = data.get("runtimes") if isinstance(data.get("runtimes"), list) else []
    blockers = data.get("blockers") if isinstance(data.get("blockers"), list) else []

    admitted = [item for item in runtimes if isinstance(item, dict) and item.get("admitted")]
    quarantined = [
        item
        for item in runtimes
        if isinstance(item, dict) and item.get("quarantine_reason")
    ]
    excluded = [
        item
        for item in runtimes
        if isinstance(item, dict) and item.get("disposition") == "excluded"
    ]

    shadow_healthy = (
        _get(data, "shadow.assistx_health") == "pass"
        and _get(data, "shadow.router_health") == "pass"
    )
    cutover_recommended = _get(data, "cutover.recommended") is True
    status = str(data.get("status") or "unknown").upper()

    lines = [
        f"# Reconciliation migration report — {data.get('migration_id', 'unknown')}",
        "",
        f"Ledger: `{path}`",
        f"Ledger SHA-256: `{_sha256(path)}`",
        "",
        "```text",
        f"STATUS: {status}",
        f"PRODUCTION_CHANGED: {_yes_no(data.get('production_changed'))}",
        f"PUBLIC_INFERENCE_FOUND: {_yes_no(data.get('public_inference_found'))}",
        f"SHADOW_STACK_HEALTHY: {_yes_no(shadow_healthy)}",
        f"RUNTIME_IDENTITY_GATE: {_gate(checks.get('runtime_identity'))}",
        f"CAPACITY_GATE: {_gate(checks.get('slot_capacity'))}",
        f"STATE_AUTHORITY_GATE: {_gate(checks.get('state_authority'))}",
        f"HERMES_SYNTHETIC_GATE: {_gate(checks.get('hermes_synthetic_task'))}",
        f"ROLLBACK_REHEARSAL: {_gate(checks.get('rollback_rehearsal'))}",
        f"CUTOVER_RECOMMENDED: {_yes_no(cutover_recommended)}",
        "```",
        "",
        "## Repositories",
        "",
    ]

    repositories = data.get("repositories")
    if isinstance(repositories, dict) and repositories:
        for name, record in sorted(repositories.items()):
            if not isinstance(record, dict):
                continue
            lines.append(
                f"- **{name}** — `{record.get('commit_sha') or 'unknown'}`; "
                f"branch `{record.get('branch') or 'unknown'}`; clean `{record.get('clean')}`"
            )
    else:
        lines.append("- No repository revisions recorded.")

    lines.extend(["", "## Runtime disposition", ""])
    if admitted:
        lines.append("### Admitted")
        for item in admitted:
            lines.append(
                f"- `{item.get('runtime_instance_id') or 'unknown'}` / "
                f"`{item.get('model_key') or 'unknown'}` — slots "
                f"`{item.get('parallel_slots', 'unknown')}`, expires "
                f"`{item.get('expires_at') or 'unknown'}`"
            )
    else:
        lines.append("- No runtime is admitted.")

    if quarantined:
        lines.extend(["", "### Quarantined"])
        for item in quarantined:
            lines.append(
                f"- `{item.get('runtime_node_id') or 'unknown'}` — "
                f"{item.get('quarantine_reason')}"
            )

    if excluded:
        lines.extend(["", "### Excluded"])
        for item in excluded:
            lines.append(
                f"- `{item.get('runtime_node_id') or 'unknown'}` — "
                f"{item.get('quarantine_reason') or 'policy exclusion'}"
            )

    lines.extend(["", "## Evidence and checks", ""])
    for name in sorted(checks):
        lines.append(f"- `{name}`: **{checks[name]}**")

    lines.extend(["", "## Blockers", ""])
    if blockers:
        for blocker in blockers:
            lines.append(f"- {blocker}")
    else:
        lines.append("- None recorded.")

    lines.extend(["", "## Required approvals", ""])
    approvals = data.get("approvals")
    if isinstance(approvals, dict):
        for name, record in approvals.items():
            if not isinstance(record, dict):
                continue
            required = bool(record.get("required"))
            approved_by = record.get("approved_by") or "not approved"
            approved_at = record.get("approved_at") or "not recorded"
            lines.append(
                f"- `{name}` — required `{required}`; by `{approved_by}`; at `{approved_at}`"
            )

    lines.extend(
        [
            "",
            "## Safety statement",
            "",
            "This report is derived from the operator-owned ledger. It does not authorize "
            "production changes. Run the ledger validator and obtain the required explicit "
            "operator approval before cutover.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("state_file", nargs="?", default="deploy/reconciliation/migration-state.yaml")
    parser.add_argument("--output", default="artifacts/reconciliation-report.md")
    args = parser.parse_args()

    path = Path(args.state_file)
    if not path.exists():
        print(f"migration state file not found: {path}", file=sys.stderr)
        return 2
    try:
        data = _load(path)
    except Exception as exc:
        print(f"cannot read migration state: {exc}", file=sys.stderr)
        return 2

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(data, path), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
