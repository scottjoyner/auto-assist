#!/usr/bin/env python3
"""Export OpenCode SQLite sessions into a durable, read-only trace ledger.

The exporter never reads provider credentials and never mutates OpenCode state.
It can reconcile sessions launched outside the free-subagent supervisor so model,
worktree, token/cost, tool, and Git provenance remain auditable.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
import tempfile
from typing import Any


def _json(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _git(directory: str) -> dict[str, Any]:
    path = pathlib.Path(directory)
    if not path.exists():
        return {"is_git": False}
    try:
        top = subprocess.check_output(
            ["git", "-C", directory, "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        branch = subprocess.check_output(
            ["git", "-C", directory, "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        head = subprocess.check_output(
            ["git", "-C", directory, "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        status = subprocess.check_output(
            ["git", "-C", directory, "status", "--porcelain"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return {
            "is_git": True,
            "root": top,
            "branch": branch,
            "head": head,
            "dirty_paths": len([line for line in status.splitlines() if line]),
        }
    except Exception:
        return {"is_git": False}


MAX_EVENTS_PER_SESSION = 10_000

ROUTE_ALIAS_TOKENS = frozenset({"free", "auto", "router", "any", "best", "default"})


def _route_kind(model_id: str) -> str:
    """Classify a requested model as exact or router-selected alias."""
    mid = str(model_id or "").strip()
    if "/" in mid:
        leaf = mid.rsplit("/", 1)[-1].lower().split(":")[0]
        if leaf in ROUTE_ALIAS_TOKENS:
            return "alias"
    return "exact"


def _model_attribution(requested: str | None, resolved: str | None) -> dict[str, Any]:
    """Return fail-closed model attribution metadata.

    A provider-resolved model is strongest. Otherwise an exact requested model
    is attributable by contract. Router aliases without a resolved upstream
    model remain explicitly unresolved.
    """
    requested = str(requested or "").strip() or None
    resolved = str(resolved or "").strip() or None
    kind = _route_kind(requested or "")
    if resolved:
        return {
            "route_kind": kind,
            "effective_model": resolved,
            "model_attribution": "resolved-upstream",
            "model_identity_complete": True,
        }
    if requested and kind == "exact":
        return {
            "route_kind": kind,
            "effective_model": requested,
            "model_attribution": "exact-request",
            "model_identity_complete": True,
        }
    return {
        "route_kind": kind,
        "effective_model": None,
        "model_attribution": "unresolved-router-alias",
        "model_identity_complete": False,
    }


def _objective_digest(con: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    row = con.execute(
        """
        SELECT p.data
        FROM part p
        JOIN message m ON m.id = p.message_id
        WHERE p.session_id = ?
        ORDER BY p.time_created ASC
        LIMIT ?
        """,
        (session_id, MAX_EVENTS_PER_SESSION),
    ).fetchall()
    texts: list[str] = []
    for (raw,) in row:
        part = _json(raw)
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
            if texts:
                break
    if not texts:
        return {"sha256": None, "chars": 0}
    text = texts[0]
    return {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "chars": len(text),
    }


def export_sessions(
    db_path: pathlib.Path, *, session_id: str | None = None,
    title_prefix: str | None = None, max_sessions: int = 500
) -> list[dict[str, Any]]:
    """Return bounded, source-read-only v1 trace summaries.

    Predicates run inside SQLite before any message/part traversal. This avoids
    dragging the full OpenCode history into memory for a single-session receipt.
    """
    if not 1 <= max_sessions <= 5000:
        raise ValueError("max_sessions must be between 1 and 5000")
    con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    clauses, parameters = [], []
    if session_id is not None:
        clauses.append("id = ?")
        parameters.append(session_id)
    if title_prefix is not None:
        # A prefix is a LITERAL string; '%' and '_' do not become SQL wildcards.
        # substr is case-sensitive and treats '%'/'_' as literal characters.
        clauses.append("substr(title, 1, length(?)) = ?")
        parameters.extend([title_prefix, title_prefix])
    sql = """SELECT id, parent_id, directory, title, model, cost,
                tokens_input, tokens_output, tokens_reasoning,
                tokens_cache_read, tokens_cache_write,
                time_created, time_updated FROM session"""
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY time_created ASC, id ASC LIMIT ?"
    sessions = con.execute(sql, (*parameters, max_sessions + 1)).fetchall()
    if len(sessions) > max_sessions:
        con.close()
        raise ValueError("trace scope exceeds max_sessions; filter by --session-id")

    records: list[dict[str, Any]] = []
    for session in sessions:
        sid = session["id"]
        model = _json(session["model"])

        messages = con.execute(
            "SELECT data FROM message WHERE session_id = ? ORDER BY time_created LIMIT ?",
            (sid, MAX_EVENTS_PER_SESSION + 1),
        ).fetchall()
        if len(messages) > MAX_EVENTS_PER_SESSION:
            raise ValueError("message event limit exceeded for selected session")
        roles: collections.Counter[str] = collections.Counter()
        assistant_errors: list[str] = []
        for (raw,) in messages:
            msg = _json(raw)
            role = msg.get("role")
            if isinstance(role, str):
                roles[role] += 1
            err = msg.get("error")
            if err:
                assistant_errors.append(err if isinstance(err, str) else json.dumps(err, sort_keys=True))

        parts = con.execute(
            "SELECT data FROM part WHERE session_id = ? ORDER BY time_created LIMIT ?",
            (sid, MAX_EVENTS_PER_SESSION + 1),
        ).fetchall()
        if len(parts) > MAX_EVENTS_PER_SESSION:
            raise ValueError("part event limit exceeded for selected session")
        part_types: collections.Counter[str] = collections.Counter()
        tools: collections.Counter[str] = collections.Counter()
        finish_reasons: list[str] = []
        for (raw,) in parts:
            part = _json(raw)
            typ = part.get("type")
            if isinstance(typ, str):
                part_types[typ] += 1
            if typ == "tool":
                tool = part.get("tool") or part.get("name")
                if isinstance(tool, str):
                    tools[tool] += 1
            if typ == "step-finish" and isinstance(part.get("reason"), str):
                finish_reasons.append(part["reason"])

        directory = session["directory"]
        requested_model = model.get("id") or model.get("modelID")
        resolved_model = model.get("resolvedID") or model.get("resolved_id") or model.get("resolvedModelID")
        attribution = _model_attribution(requested_model, resolved_model)
        records.append(
            {
                "schema": "opencode-session-trace/v1",
                "session_id": sid,
                "parent_session_id": session["parent_id"],
                "title": session["title"],
                "directory": directory,
                "provider": model.get("providerID"),
                "model": requested_model,
                "requested_model": requested_model,
                "resolved_model": resolved_model,
                **attribution,
                "variant": model.get("variant"),
                "cost": session["cost"],
                "tokens": {
                    "input": session["tokens_input"],
                    "output": session["tokens_output"],
                    "reasoning": session["tokens_reasoning"],
                    "cache_read": session["tokens_cache_read"],
                    "cache_write": session["tokens_cache_write"],
                },
                "time_created": session["time_created"],
                "time_updated": session["time_updated"],
                "messages": dict(sorted(roles.items())),
                "parts": dict(sorted(part_types.items())),
                "tools": dict(sorted(tools.items())),
                "patch_parts": part_types.get("patch", 0),
                "last_finish_reason": finish_reasons[-1] if finish_reasons else None,
                "assistant_error_count": len(assistant_errors),
                "objective": _objective_digest(con, sid),
                "git": _git(directory),
            }
        )
    con.close()
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--title-prefix")
    parser.add_argument("--session-id", help="exact OpenCode session ID")
    parser.add_argument("--max-sessions", type=int, default=500)
    args = parser.parse_args()

    try:
        records = export_sessions(
            args.db, session_id=args.session_id,
            title_prefix=args.title_prefix, max_sessions=args.max_sessions
        )
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"error": "read_only_trace_export_failed",
                          "error_type": type(error).__name__}, sort_keys=True))
        return 1
    if args.session_id is not None and not records:
        print(json.dumps({"error": "requested_session_not_found"}))
        return 1
    if args.out.is_symlink():
        print(json.dumps({"error": "symlink_output_refused"}))
        return 1
    # The JSONL ledger is private and atomic; a failed write must not leave an
    # incomplete receipt or truncate a previously accepted export.
    temp_path = None
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.out.parent,
            prefix=".opencode-trace-", suffix=".tmp", delete=False
        ) as handle:
            temp_path = pathlib.Path(handle.name)
            for record in records:
                handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, args.out)
        temp_path = None
    except (OSError, TypeError, ValueError, OverflowError) as error:
        print(json.dumps({"error": "private_trace_write_failed",
                          "error_type": type(error).__name__}, sort_keys=True))
        return 1
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    missing_model = [r["session_id"] for r in records if not r.get("provider") or not r.get("model")]
    unresolved = [r["session_id"] for r in records if not r.get("model_identity_complete")]
    summary = {
        "records": len(records),
        "with_requested_model_identity": len(records) - len(missing_model),
        "missing_requested_model_identity": len(missing_model),
        "fully_attributed_model_records": len(records) - len(unresolved),
        "unresolved_model_attribution_records": len(unresolved),
        "output": str(args.out),
    }
    print(json.dumps(summary, sort_keys=True))
    return 1 if missing_model else 0


if __name__ == "__main__":
    raise SystemExit(main())
