#!/usr/bin/env python3
"""Read-only free-pool status CLI for operators.

Reads the free-pool projection and central subagent ledgers, emits
machine-readable JSON with proven/trial/blocked-low-fitness/unavailable
models, active runs, recent provider failures, budget terminations,
and Z.AI reserve policy.

No provider network calls, no secret values, no routing/admission changes.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import UTC, datetime
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from free_pool_projection import (
    FAILURE_STATES,
    _capability_qualified,
    _event_state,
    _event_timestamp,
    _latest_model_state,
    _model_id,
    _pid_matches_record,
    load_qualification_records,
    load_state_records,
    model_fitness_projection,
)

DEFAULT_STATE_ROOT = pathlib.Path.home() / ".local" / "state" / "auto-assist" / "subagents"
DEFAULT_PROJECTION = pathlib.Path.home() / ".local" / "state" / "auto-assist" / "subagents" / "free-pool-projection.json"


def _parse_time(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 10_000_000_000 else float(value)
    if not isinstance(value, str) or not value.strip():
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    except ValueError:
        return 0.0


def _read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def load_projection(projection_path: pathlib.Path | None = None) -> dict[str, Any]:
    path = projection_path or DEFAULT_PROJECTION
    if not path.exists():
        return {"schema": "free-pool-projection/v1", "error": "projection not found"}
    return json.loads(path.read_text())


def classify_runs(
    state_records: list[dict[str, Any]],
    *,
    process_alive=_pid_matches_record,
) -> dict[str, list[dict[str, Any]]]:
    active: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    budget_term: list[dict[str, Any]] = []
    now = datetime.now(UTC).timestamp()

    for record in state_records:
        status = str(record.get("status") or "").lower()
        pid = record.get("pid") or record.get("opencode_pid")
        is_alive = False
        if pid:
            try:
                is_alive = process_alive(record)
            except Exception:
                is_alive = False

        entry = {
            "provider": str(record.get("provider") or ""),
            "model": _model_id(record),
            "session": str(record.get("session") or ""),
            "pid": int(pid) if pid else None,
            "worktree": str(record.get("worktree") or ""),
            "status": status,
            "failure_class": str(record.get("failure_class") or ""),
            "tokens": dict(record.get("tokens") or {}),
            "tools": dict(record.get("tools") or {}),
            "title": str(record.get("title") or ""),
            "objective": str(record.get("objective") or "")[:200],
            "created_at": str(record.get("created_at") or ""),
            "updated_at": str(record.get("updated_at") or ""),
            "process_alive": is_alive,
        }

        if status == "running" and is_alive:
            entry["elapsed_seconds"] = round(now - _parse_time(record.get("started_at") or record.get("created_at")), 1)
            active.append(entry)
        elif status in ("aborted", "aborted_stale_process", "failed"):
            failed.append(entry)
        elif status == "completed" and record.get("failure_class"):
            failed.append(entry)

        # Budget terminations: finish_reason or objective mentions budget
        finish = str(record.get("last_finish_reason") or "")
        if "budget" in finish.lower() or "budget" in str(record.get("objective") or "").lower():
            budget_term.append(entry)

    return {"active": active, "failed": failed, "budget_terminations": budget_term}


def classify_models(projection: dict[str, Any]) -> dict[str, list[str]]:
    kilo = projection.get("kilo", {})
    models = kilo.get("models", [])
    return {
        "proven": [m["id"] for m in models if m.get("recommendation_tier") == "proven"],
        "trial": [m["id"] for m in models if m.get("recommendation_tier") == "trial"],
        "blocked_low_fitness": [m["id"] for m in models if m.get("recommendation_tier") == "blocked_low_fitness"],
        "blocked_unavailable": [m["id"] for m in models if m.get("recommendation_tier") == "blocked_unavailable"],
    }


def build_status(
    *,
    projection_path: pathlib.Path | None = None,
    state_root: pathlib.Path = DEFAULT_STATE_ROOT,
    live_only: bool = False,
    process_alive=None,
) -> dict[str, Any]:
    if process_alive is None:
        process_alive = _pid_matches_record
    projection = load_projection(projection_path)
    state_records = load_state_records(state_root)
    qualification_records = load_qualification_records(state_root)

    if live_only:
        state_records = [r for r in state_records if str(r.get("status") or "").lower() == "running"]

    runs = classify_runs(state_records, process_alive=process_alive)
    model_tiers = classify_models(projection)

    # Recent provider failures (last 24h)
    now = datetime.now(UTC).timestamp()
    recent_failures = [
        r for r in runs["failed"]
        if r.get("failure_class") and now - _parse_time(r.get("updated_at")) < 86400
    ]

    # Z.AI reserve policy
    zai_policy = projection.get("zai_policy", "unknown")

    # Evidence freshness
    latest_update = max(
        (_parse_time(r.get("updated_at")) for r in state_records),
        default=0.0,
    )
    evidence_age_seconds = round(now - latest_update, 1) if latest_update else None
    evidence_fresh = evidence_age_seconds is not None and evidence_age_seconds < 3600

    return {
        "schema": "free-pool-status/v1",
        "read_only": True,
        "evidence_fresh": evidence_fresh,
        "evidence_age_seconds": evidence_age_seconds,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "models": model_tiers,
        "runs": runs,
        "recent_provider_failures": recent_failures,
        "zai_reserve_policy": zai_policy,
        "fitness_policy": projection.get("kilo", {}).get("fitness_policy", {}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Free-pool status CLI (read-only).")
    parser.add_argument("--state-root", type=pathlib.Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--projection", type=pathlib.Path, default=None)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--live-only", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = build_status(
            projection_path=args.projection,
            state_root=args.state_root,
            live_only=args.live_only,
        )
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2

    text = json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())