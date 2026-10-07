"""Free subagent supervision slice — scale actions (terminate / admit)."""

from __future__ import annotations

import json
import os
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
from free_subagent_state import register_subagent


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(entry: dict[str, Any]) -> None:
    print(json.dumps(entry, sort_keys=True, ensure_ascii=False), file=sys.stderr)


def terminate_subagent(pid: int, session: str = "", sig: int = signal.SIGTERM) -> dict[str, Any]:
    result = None
    try:
        os.kill(pid, sig)
        result = {"pid": pid, "session": session, "action": "terminated", "signal": sig}
    except ProcessLookupError:
        result = {"pid": pid, "session": session, "action": "already_gone", "signal": sig}
    except PermissionError:
        result = {"pid": pid, "session": session, "action": "permission_denied", "signal": sig}
    except OSError as exc:
        result = {"pid": pid, "session": session, "action": "error", "error": str(exc), "signal": sig}
    _log({"ts": _now_iso(), "event": "terminate", **result})
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
    record = {
        "title": f"admitted-{model.get('id', '').replace('/', '-')}",
        "objective": "auto-admitted by free_subagent_supervisor",
        "model": model.get("id", ""),
        "worktree": f"/tmp/admitted-{model.get('id', '').replace('/', '-')}",
        "pid": 0,
        "session": "",
        "status": "admitted",
    }
    try:
        recs = register_subagent(record, state_path)
        result = {"admitted": True, "model": model.get("id"), "total_records": len(recs), "record": recs[-1]}
    except Exception as exc:
        result = {"admitted": False, "model": model.get("id"), "error": str(exc)}
    _log({"ts": _now_iso(), "event": "admit", **result})
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
        models = enumerate_free_models()
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
    }