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
import os
import pathlib
import stat
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


def _read_bounded_local_object(path: pathlib.Path) -> dict[str, Any]:
    """Only regular, bounded local JSON objects; never follow the final symlink."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or not 0 < meta.st_size <= 2 * 1024 * 1024:
            raise ValueError("local_file_invalid")
        with os.fdopen(fd, "rb", closefd=False) as f:
            payload = f.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError("local_file_oversized")
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("local_json_not_object")
        return data
    finally:
        os.close(fd)


def load_projection(projection_path: pathlib.Path | None = None) -> dict[str, Any]:
    path = projection_path or DEFAULT_PROJECTION
    try:
        return _read_bounded_local_object(path)
    except (OSError, ValueError, UnicodeError):
        return {"schema": "free-pool-projection/v1", "error": "projection unavailable"}


def _local_file_recent(path: pathlib.Path, now: float, max_age: int = 3600) -> bool:
    """Advisory only. Refuse missing, symlinked, future, stale or oversized local evidence."""
    try:
        if path.is_symlink():
            return False
        meta = path.stat()
    except OSError:
        return False
    return (
        stat.S_ISREG(meta.st_mode)
        and 0 < meta.st_size <= 2 * 1024 * 1024
        and -30 <= now - meta.st_mtime <= max_age
    )


def projection_evidence_recent(
    projection: dict[str, Any],
    projection_path: pathlib.Path,
    state_root: pathlib.Path,
    *,
    now: float,
) -> tuple[bool, tuple[str, ...]]:
    """Require BOTH snapshot and completed refresh evidence; never grants route admission."""
    reasons: list[str] = []
    if (
        projection.get("schema") != "free-pool-projection/v1"
        or projection.get("read_only") is not True
        or projection.get("authoritative_routing_unchanged") is not True
    ):
        reasons.append("projection_contract_missing")
    if not _local_file_recent(projection_path, now):
        reasons.append("projection_missing_or_stale")

    refresh_path = state_root / "last-free-pool-refresh.json"
    if not _local_file_recent(refresh_path, now):
        reasons.append("refresh_missing_or_stale")
    else:
        try:
            refresh = _read_bounded_local_object(refresh_path)
            if not isinstance(refresh, dict) or (
                refresh.get("schema") != "free-pool-refresh-run/v1"
                or refresh.get("executed") is not True
                or refresh.get("paid_fallback") is not False
            ):
                reasons.append("refresh_contract_invalid")
            else:
                stamp = refresh.get("finished_at")
                if not isinstance(stamp, str) or not stamp.endswith(("+00:00", "Z")):
                    reasons.append("refresh_completion_invalid")
                else:
                    age = now - _parse_time(stamp)
                    if not -30 <= age <= 3600:
                        reasons.append("refresh_completion_stale")
        except (OSError, ValueError, TypeError):
            reasons.append("refresh_invalid")
    return not reasons, tuple(sorted(set(reasons)))


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
    # An isolated state root must not silently read the live pool projection.
    effective_projection_path = (
        projection_path
        if projection_path is not None
        else state_root / "free-pool-projection.json"
    )
    projection = load_projection(effective_projection_path)
    state_records = load_state_records(state_root)
    qualification_records = load_qualification_records(state_root)

    if live_only:
        state_records = [r for r in state_records if str(r.get("status") or "").lower() == "running"]

    runs = classify_runs(state_records, process_alive=process_alive)
    model_tiers = classify_models(projection)
    now = datetime.now(UTC).timestamp()
    projection_fresh, projection_blockers = projection_evidence_recent(
        projection, effective_projection_path, state_root, now=now
    )
    # Do not publish stale "proven"/"trial" models as live candidates. Preserve
    # only counts for historical diagnosis; no local receipt grants admission.
    historical_model_counts = {
        "proven": len(model_tiers["proven"]),
        "trial": len(model_tiers["trial"]),
    }
    if not projection_fresh:
        model_tiers["proven"] = []
        model_tiers["trial"] = []

    # Recent provider failures (last 24h)
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
    lifecycle_recent = (
        evidence_age_seconds is not None
        and -30 <= evidence_age_seconds <= 3600
    )
    evidence_fresh = lifecycle_recent and projection_fresh

    return {
        "schema": "free-pool-status/v1",
        "read_only": True,
        "evidence_fresh": evidence_fresh,
        "evidence_age_seconds": evidence_age_seconds,
        "lifecycle_recent": lifecycle_recent,
        "projection_fresh": projection_fresh,
        "projection_blockers": list(projection_blockers),
        "historical_model_counts": historical_model_counts,
        "dispatch_allowed": False,
        "account_entitlement_proven": False,
        "shared_quota_lease_proven": False,
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