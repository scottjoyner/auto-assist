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
import pathlib
import sqlite3
import subprocess
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
        """,
        (session_id,),
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


def export_sessions(db_path: pathlib.Path) -> list[dict[str, Any]]:
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    sessions = con.execute(
        """
        SELECT id, parent_id, directory, title, model, cost,
               tokens_input, tokens_output, tokens_reasoning,
               tokens_cache_read, tokens_cache_write,
               time_created, time_updated
        FROM session
        ORDER BY time_created ASC
        """
    ).fetchall()

    records: list[dict[str, Any]] = []
    for session in sessions:
        sid = session["id"]
        model = _json(session["model"])

        messages = con.execute(
            "SELECT data FROM message WHERE session_id = ? ORDER BY time_created",
            (sid,),
        ).fetchall()
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
            "SELECT data FROM part WHERE session_id = ? ORDER BY time_created",
            (sid,),
        ).fetchall()
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
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--title-prefix")
    args = parser.parse_args()

    records = export_sessions(args.db)
    if args.title_prefix:
        records = [r for r in records if str(r.get("title", "")).startswith(args.title_prefix)]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

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
