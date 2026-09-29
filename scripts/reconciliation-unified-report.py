#!/usr/bin/env python3
"""Build one read-only, machine-readable reconciliation report.

This joins the operator-owned migration ledger with the candidate-only Tailscale
inventory. It does not probe, admit, route, claim, or mutate any system.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"input is not a file: {path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected an object in {path}")
    return value


def _load_candidates(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"schema_version": None, "authority": "not_provided", "nodes": []}
    if not path.is_file():
        raise ValueError(f"candidate inventory is not a file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected an object in {path}")
    nodes = value.get("nodes", [])
    if not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes):
        raise ValueError("candidate inventory nodes must be a list of objects")
    return value


def _source(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return {"path": str(path), "sha256": _sha256(path)}


def build_report(
    ledger_path: Path,
    *,
    candidates_path: Path | None = None,
    captured_at: str | None = None,
) -> dict[str, Any]:
    ledger = _load_object(ledger_path)
    candidates = _load_candidates(candidates_path)
    runtimes = ledger.get("runtimes", [])
    if not isinstance(runtimes, list):
        raise ValueError("ledger runtimes must be a list")
    blockers = ledger.get("blockers", [])
    if not isinstance(blockers, list):
        raise ValueError("ledger blockers must be a list")
    candidate_nodes = candidates.get("nodes", [])
    online_candidates = [node for node in candidate_nodes if node.get("online") is True]
    admitted = [item for item in runtimes if isinstance(item, dict) and item.get("admitted") is True]
    quarantined = [
        item for item in runtimes
        if isinstance(item, dict) and item.get("quarantine_reason")
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at": captured_at or datetime.now(UTC).isoformat(),
        "authority": {
            "artifact": "read_only_reconciliation_report",
            "ledger": "operator_owned_migration_ledger",
            "candidate_inventory": candidates.get("authority", "unknown"),
            "does_not_authorize_production_changes": True,
        },
        "sources": {
            "ledger": _source(ledger_path),
            "candidate_inventory": _source(candidates_path),
        },
        "summary": {
            "ledger_status": ledger.get("status", "unknown"),
            "production_changed": ledger.get("production_changed"),
            "candidate_nodes": len(candidate_nodes),
            "online_candidate_nodes": len(online_candidates),
            "admitted_runtimes": len(admitted),
            "quarantined_runtimes": len(quarantined),
            "blockers": len(blockers),
            "tailnet_is_reachability_only": candidates.get("authority") == "candidate_reachability_only",
        },
        "ledger": ledger,
        "candidate_inventory": candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build_report(args.ledger, candidates_path=args.candidates)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
