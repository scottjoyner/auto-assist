"""Free subagent supervision slice — session discovery and anomaly detection."""

from __future__ import annotations

import json
import pathlib
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any


def _opencode_db_uri() -> str:
    db_path = pathlib.Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    return f"file:{db_path}?mode=ro"


def discover_live_sessions(query_only: bool = True) -> list[dict[str, Any]]:
    db_uri = _opencode_db_uri()
    conn = sqlite3.connect(db_uri, uri=True, check_same_thread=False)
    try:
        conn.execute("PRAGMA query_only = ON")
        try:
            conn.execute("CREATE TEMP TABLE _assert_write_fail (id INTEGER)")
            raise AssertionError("Write succeeded despite mode=ro and PRAGMA query_only")
        except sqlite3.OperationalError:
            pass
        cur = conn.cursor()
        now_ms = int(time.time() * 1000)
        seven_days_ms = 7 * 24 * 60 * 60 * 1000
        cur.execute(
            "SELECT id, title, slug, directory, agent, model, time_updated, time_archived FROM session WHERE time_updated > ? ORDER BY time_updated DESC LIMIT 50",
            (now_ms - seven_days_ms,),
        )
        cols = [d[0] for d in cur.description]
        rows = []
        for row in cur.fetchall():
            raw = dict(zip(cols, row, strict=False))
            session_id = raw.get("id") or raw.get("slug") or ""
            worktree = raw.get("directory") or ""
            agent = raw.get("agent") or ""
            model_raw = raw.get("model")
            model_name = ""
            provider = agent
            try:
                if isinstance(model_raw, str) and model_raw:
                    parsed = json.loads(model_raw)
                    if isinstance(parsed, dict):
                        model_name = parsed.get("id") or parsed.get("name") or ""
                        if parsed.get("providerID"):
                            provider = parsed.get("providerID")
                        elif parsed.get("provider"):
                            provider = parsed.get("provider")
            except Exception:
                model_name = str(model_raw) if model_raw is not None else ""
            updated_at_ms = raw.get("time_updated")
            updated_at = ""
            if updated_at_ms is not None:
                try:
                    ts = datetime.fromtimestamp(updated_at_ms / 1000.0, tz=UTC)
                    updated_at = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    updated_at = str(updated_at_ms)
            status = "archived" if raw.get("time_archived") is not None else "active"
            rows.append(
                {
                    "session": session_id,
                    "worktree": worktree,
                    "model": model_name,
                    "provider": provider,
                    "updated_at": updated_at,
                    "status": status,
                    "slug": raw.get("slug"),
                    "title": raw.get("title"),
                }
            )
        return rows
    finally:
        conn.close()


def detect_duplicate_worktrees(
    records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    from free_subagent_state import load_state

    recs = records if records is not None else load_state()
    active = [
        r
        for r in recs
        if str(r.get("status") or "").lower() in ("active", "running", "in_progress")
        and not (r.get("source") == "sqlite_readonly" and not r.get("pid"))
    ]
    conflicts: list[dict[str, Any]] = []
    worktree_map: dict[str, list[dict[str, Any]]] = {}
    for r in active:
        wt = str(r.get("worktree") or "").strip()
        if wt:
            worktree_map.setdefault(wt, []).append(r)
    for wt, group in worktree_map.items():
        if len(group) > 1:
            conflicts.append(
                {
                    "worktree": wt,
                    "conflicting_records": len(group),
                    "pids": [r.get("pid") for r in group],
                    "sessions": [r.get("session") for r in group],
                    "titles": [r.get("title") for r in group],
                }
            )
    return conflicts


def detect_looping_records(
    records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    from free_subagent_state import load_state

    recs = records if records is not None else load_state()
    session_counts: dict[str, int] = {}
    for r in recs:
        sess = str(r.get("session") or "").strip()
        if sess:
            session_counts[sess] = session_counts.get(sess, 0) + 1
    loops = []
    for r in recs:
        sess = str(r.get("session") or "").strip()
        if sess and session_counts.get(sess, 0) > 1:
            loops.append(
                {
                    "session": sess,
                    "record_title": r.get("title"),
                    "record_pid": r.get("pid"),
                    "record_status": r.get("status"),
                    "occurrences": session_counts[sess],
                }
            )
    seen = set()
    deduped = []
    for item in loops:
        if item["session"] not in seen:
            seen.add(item["session"])
            deduped.append(
                {
                    "session": item["session"],
                    "occurrences": item["occurrences"],
                    "titles": [
                        rec.get("title")
                        for rec in recs
                        if str(rec.get("session") or "") == item["session"]
                    ],
                    "pids": [
                        rec.get("pid")
                        for rec in recs
                        if str(rec.get("session") or "") == item["session"]
                    ],
                }
            )
    return deduped


def detect_stale_records(
    records: list[dict[str, Any]] | None = None, max_age_minutes: int = 30
) -> list[dict[str, Any]]:
    from free_subagent_state import load_state

    recs = records if records is not None else load_state()
    stale: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for r in recs:
        ts_str = str(r.get("updated_at") or r.get("created_at") or "").strip()
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            age_minutes = (now - ts).total_seconds() / 60.0
            if age_minutes > max_age_minutes:
                stale.append(
                    {
                        "session": r.get("session"),
                        "worktree": r.get("worktree"),
                        "updated_at": ts_str,
                        "status": r.get("status"),
                        "age_minutes": round(age_minutes, 2),
                    }
                )
        except Exception:
            continue
    seen = set()
    deduped = []
    for item in stale:
        key = (str(item.get("session") or ""), str(item.get("worktree") or ""))
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _annotate_trace_session(record: dict[str, Any]) -> dict[str, Any]:
    from free_subagent_routing import route_kind

    annotated = dict(record)
    requested = str(annotated.get("requested_model") or annotated.get("model") or "")
    resolved = annotated.get("resolved_model")
    annotated.setdefault("requested_model", requested)
    annotated.setdefault("resolved_model", resolved)
    kind = route_kind(requested)
    annotated["route_kind"] = kind
    if resolved:
        annotated["effective_model"] = resolved
        annotated["model_attribution"] = "resolved-upstream"
        annotated["model_identity_complete"] = True
    elif requested and kind == "exact":
        annotated["effective_model"] = requested
        annotated["model_attribution"] = "exact-request"
        annotated["model_identity_complete"] = True
    else:
        annotated["effective_model"] = None
        annotated["model_attribution"] = "unresolved-router-alias"
        annotated["model_identity_complete"] = False
    return annotated