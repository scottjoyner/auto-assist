"""Free subagent supervision slice — scale actions (terminate / admit)."""

from __future__ import annotations

import json
import pathlib
import signal
import sys
from datetime import UTC, datetime
from typing import Any

from free_subagent_discover import (
    detect_duplicate_worktrees,
    detect_looping_records,
    detect_stale_records,
)
from free_subagent_routing import classify_free_model, enumerate_free_models


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(entry: dict[str, Any]) -> None:
    print(json.dumps(entry, sort_keys=True, ensure_ascii=False), file=sys.stderr)


def terminate_subagent(pid: int, session: str = "", sig: int = signal.SIGTERM) -> dict[str, Any]:
    """Deny raw PID signaling until independent process ownership is provable.

    Registry records and supervisor projections are editable observation data,
    not an authority to signal a host PID. PID reuse, a forged session, or a
    restored state file would otherwise allow killing unrelated processes.
    No environment toggle or caller-provided boolean is sufficient custody.
    """
    result = {
        "pid": pid,
        "session": session,
        "action": "denied_unverified_process_owner",
        "signal": sig,
        "reason": "resource_owned_process_identity_witness_required",
    }
    _log({"ts": _now_iso(), "event": "terminate_denied", **result})
    return result


def cleanup_stale(
    records: list[dict[str, Any]] | None = None,
    max_age_minutes: int = 30,
) -> list[dict[str, Any]]:
    stale = detect_stale_records(records, max_age_minutes=max_age_minutes)
    recs = records or []
    targets = []
    for r in stale:
        sess = str(r.get("session") or "")
        match = next((rec for rec in recs if str(rec.get("session") or "") == sess), None)
        pid = match.get("pid") if match else None
        if pid is not None and int(pid) != 0:
            targets.append({"pid": int(pid), "session": sess, "reason": "stale", "age_minutes": r.get("age_minutes")})
    return targets


def cleanup_duplicates(
    records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    conflicts = detect_duplicate_worktrees(records)
    targets = []
    for conflict in conflicts:
        worktree = conflict["worktree"]
        group = [r for r in (records or []) if str(r.get("worktree") or "").strip() == worktree]
        keep = group[0]
        for r in group[1:]:
            pid = r.get("pid")
            if pid is not None and int(pid) != 0:
                targets.append({"pid": int(pid), "session": r.get("session", ""), "reason": "duplicate_worktree", "worktree": worktree, "keep_pid": keep.get("pid")})
    return targets


def cleanup_looping(
    records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    loops = detect_looping_records(records)
    targets = []
    for loop in loops:
        session = loop["session"]
        group = [r for r in (records or []) if str(r.get("session") or "") == session]
        keep = group[0]
        for r in group[1:]:
            pid = r.get("pid")
            if pid is not None and int(pid) != 0:
                targets.append({"pid": int(pid), "session": session, "reason": "looping", "keep_pid": keep.get("pid")})
    return targets


def admit_subagent(
    model: dict[str, Any],
    state_path: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Deny direct admission: writable observer state is not a resource grant.

    This function is exported by free_subagent_supervisor, so active-mode
    denial in run_supervision_loop is insufficient by itself. In particular,
    no caller may manufacture an "admitted" registry row through this helper.
    The state_path argument is retained for compatibility but never opened.
    """
    model_id = model.get("id") if isinstance(model, dict) else None
    result = {
        "admitted": False,
        "model": model_id,
        "blocked": True,
        "reason": "resource_owned_process_and_admission_authority_required",
        "dispatch_authorized": False,
        "provider_calls": 0,
        "state_mutated": False,
    }
    _log({"ts": _now_iso(), "event": "admit_denied", **result})
    return result

def run_supervision_loop(
    projection: dict[str, Any],
    state_path: pathlib.Path | None = None,
    max_age_minutes: int = 30,
    dry_run: bool = True,
    admit_count: int = 1,
) -> dict[str, Any]:
    verdict = projection.get("suggested_scale_verdict", "hold")
    actions_taken: list[dict[str, Any]] = []

    _log({"ts": _now_iso(), "event": "supervision_cycle", "verdict": verdict, "dry_run": dry_run})

    if not dry_run:
        # Neither the projection nor the local state file can authorize host
        # signals or write admission records. Preserve the requested verdict
        # for diagnostics while refusing all active effects until a separate
        # resource-owned, non-restorable execution authority is implemented.
        return {
            "verdict": "hold",
            "requested_verdict": verdict,
            "dry_run": False,
            "actions_taken": [],
            "action_count": 0,
            "blocked": True,
            "reason": "resource_owned_process_and_admission_authority_required",
            "model_source": None,
        }

    if verdict == "hold":
        records = projection.get("subagent_records", 0)
        if records:
            from free_subagent_state import load_state
            recs = load_state(state_path)
            for batch_name, batch in [
                ("stale", cleanup_stale(recs, max_age_minutes=max_age_minutes)),
                ("duplicate", cleanup_duplicates(recs)),
                ("looping", cleanup_looping(recs)),
            ]:
                for target in batch:
                    if dry_run:
                        target["action"] = "dry_run"
                    else:
                        result = terminate_subagent(target["pid"], target["session"])
                        target["action"] = result.get("action", "unknown")
                        target["error"] = result.get("error")
                    actions_taken.append(target)
    elif verdict == "healthy":
        ids = projection.get("free_model_ids")
        source = "projection"
        if isinstance(ids, list) and ids:
            models = [{"id": mid} for mid in ids]
        else:
            models = enumerate_free_models()
            source = "live_fallback"
        admitted = []
        for m in models[:admit_count]:
            if dry_run:
                admitted.append({"model": m.get("id"), "action": "dry_run"})
            else:
                admitted.append(admit_subagent(m, state_path))
        actions_taken.extend(admitted)

    return {
        "verdict": verdict,
        "dry_run": dry_run,
        "actions_taken": actions_taken,
        "action_count": len(actions_taken),
        "model_source": locals().get("source"),
    }