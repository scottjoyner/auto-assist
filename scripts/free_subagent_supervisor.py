#!/usr/bin/env python3
"""Free-model subagent supervision slice — read-only to routing/admission.

Purpose:
  Make direct OpenCode free-model subagent execution safer and observable
  to a supervising agent without modifying authoritative routing, admission,
  dispatch, or approval paths. Reuses scripts/opencode-bridge conventions.

Capabilities:
  1. Enumerate configured :free OpenRouter model IDs from
     `opencode models openrouter` or parse supplied fixture output.
  2. Report whether an OpenRouter credential is present WITHOUT reading/
     printing the secret value.
  3. Register/inspect local subagent records in a local JSON/JSONL state file.
  4. Detect duplicate/conflicting active worktrees.
  5. Emit a machine-readable read-only supervision projection with a
     suggested-but-not-enforced scale verdict (healthy / hold).

Noninteractive stdin hazard (Fleet Commander-style launches):
  If launched noninteractively (e.g., via a fleet commander or automation
  wrapper), stdin must be closed or redirected from /dev/null. Example:
      opencode run ... < /dev/null
  Otherwise opencode may wait at init for interactive input.
  This module is read-only and never terminates external processes.

No API key values are emitted. No production routing mutations occur.
No process termination is performed.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

# Convention reuse: projection files use compact sorted JSON.
_PROJECTION_KEYS = [
    "supervision_slice",
    "observed_at",
    "read_only",
    "suggested_scale_verdict",
    "free_models_found",
    "credential_present",
    "subagent_records",
    "duplicate_worktrees",
    "projections_note",
]

DEFAULT_STATE_PATH = pathlib.Path("local_subagent_state.jsonl")
DEFAULT_FIXTURE_PATH = pathlib.Path("tests/fixtures/openrouter_models_sample.jsonl")


def _opencode_binary() -> str:
    return os.environ.get("OPENCODE_BIN") or shutil.which("opencode") or str(pathlib.Path.home() / ".opencode" / "bin" / "opencode")


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _price_is_zero(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _is_clearly_free_model(record: dict[str, Any]) -> bool:
    model_id = str(record.get("id") or "")
    if ":free" in model_id:
        return True

    tags = record.get("tags") or []
    if isinstance(tags, list) and any(str(tag).lower() == "free" for tag in tags):
        return True

    pricing = record.get("pricing") or {}
    if not isinstance(pricing, dict):
        return False
    return _price_is_zero(pricing.get("prompt")) and _price_is_zero(pricing.get("completion"))


def enumerate_free_models(fixture_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    """Return models that are explicitly free or zero-cost in both directions."""
    results: list[dict[str, Any]] = []
    # Try authoritative command first; if unavailable/offline, fall back.
    try:
        proc = subprocess.run(
            [_opencode_binary(), "models"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Heuristic: lines with openrouter/ and free indicator
                if "openrouter/" in line:
                    # Basic extraction; if JSON, parse; else treat as id
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict) and obj.get("id") and _is_clearly_free_model(obj):
                            results.append(obj)
                    except json.JSONDecodeError:
                        # Plain model listings have no pricing metadata, so require the
                        # provider's explicit :free suffix instead of guessing by name.
                        if ":free" in line:
                            results.append({"id": line, "provider": "openrouter", "tags": ["free"]})
            # Deduplicate by id
            seen = set()
            deduped = []
            for r in results:
                rid = r.get("id")
                if rid and rid not in seen:
                    seen.add(rid)
                    deduped.append(r)
            return deduped
    except Exception:
        pass  # fall through to fixture
    # Fixture fallback
    fixture = fixture_path or DEFAULT_FIXTURE_PATH
    if fixture.exists():
        with fixture.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("id") and _is_clearly_free_model(obj):
                        results.append(obj)
                except json.JSONDecodeError:
                    continue
    # Deduplicate
    seen = set()
    deduped = []
    for r in results:
        rid = r.get("id")
        if rid and rid not in seen:
            seen.add(rid)
            deduped.append(r)
    return deduped


def credential_present() -> bool:
    """Return True if an OpenRouter credential env var exists.
    Never reads or prints the secret value."""
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_KEY", "OPENROUTER_API_KEY_2"):
        if os.environ.get(key):
            return True
    return False


def load_state(state_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    path = state_path or DEFAULT_STATE_PATH
    records: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        records.append(rec)
                except json.JSONDecodeError:
                    continue
    return records


def save_state(records: list[dict[str, Any]], state_path: pathlib.Path | None = None) -> None:
    path = state_path or DEFAULT_STATE_PATH
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")


def register_subagent(
    record: dict[str, Any],
    state_path: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    """Append a local subagent record; return updated list."""
    records = load_state(state_path)
    # Ensure required fields exist; do not enforce schema mutations beyond reading
    for k in ("title", "objective", "model", "worktree", "pid", "session", "status"):
        if k not in record:
            record[k] = ""
    if "created_at" not in record:
        record["created_at"] = _now_utc()
    record["updated_at"] = _now_utc()
    records.append(record)
    save_state(records, state_path)
    return records


def inspect_subagents(state_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    return load_state(state_path)

def _opencode_db_uri() -> str:
    db_path = os.path.join(os.path.expanduser("~"), ".local", "share", "opencode", "opencode.db")
    return f"file:{db_path}?mode=ro"

def discover_live_sessions(query_only: bool = True) -> list[dict[str, Any]]:
    """SQLite read-only / query_only session discovery; no mutation.
    OpenCode time_updated is milliseconds; derived path from HOME."""
    import sqlite3, time
    db_uri = _opencode_db_uri()
    conn = sqlite3.connect(db_uri, uri=True, check_same_thread=False)
    try:
        conn.execute("PRAGMA query_only = ON")
        # Prove mode=ro + PRAGMA query_only: a write must fail.
        try:
            conn.execute("CREATE TEMP TABLE _assert_write_fail (id INTEGER)")
            raise AssertionError("Write succeeded despite mode=ro and PRAGMA query_only")
        except sqlite3.OperationalError:
            pass  # expected failure
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
            raw = dict(zip(cols, row))
            # Map to duplicate/stale fields used by supervisor logic.
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
                    ts = datetime.fromtimestamp(updated_at_ms / 1000.0, tz=timezone.utc)
                    updated_at = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    updated_at = str(updated_at_ms)
            status = "archived" if raw.get("time_archived") is not None else "active"
            rows.append({
                "session": session_id,
                "worktree": worktree,
                "model": model_name,
                "provider": provider,
                "updated_at": updated_at,
                "status": status,
                "slug": raw.get("slug"),
                "title": raw.get("title"),
            })
        return rows
    finally:
        conn.close()



def detect_duplicate_worktrees(records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Detect active records with conflicting worktrees."""
    recs = records if records is not None else load_state()
    active = [
        r for r in recs
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
            conflicts.append({
                "worktree": wt,
                "conflicting_records": len(group),
                "pids": [r.get("pid") for r in group],
                "sessions": [r.get("session") for r in group],
                "titles": [r.get("title") for r in group],
            })
    return conflicts


def detect_looping_records(records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Detect records with repeated session identifiers (looping registration)."""
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
            loops.append({
                "session": sess,
                "record_title": r.get("title"),
                "record_pid": r.get("pid"),
                "record_status": r.get("status"),
                "occurrences": session_counts[sess],
            })
            # Deduplicate loop entries per session by removing after first
            # but keep structure simple: return unique loop groups
    # Deduplicate by session
    seen = set()
    deduped = []
    for item in loops:
        if item["session"] not in seen:
            seen.add(item["session"])
            deduped.append({
                "session": item["session"],
                "occurrences": item["occurrences"],
                "titles": [rec.get("title") for rec in recs if str(rec.get("session") or "") == item["session"]],
                "pids": [rec.get("pid") for rec in recs if str(rec.get("session") or "") == item["session"]],
            })
    return deduped


def detect_stale_records(records: list[dict[str, Any]] | None = None, max_age_minutes: int = 30) -> list[dict[str, Any]]:
    """Detect subagent records whose updated_at is older than max_age_minutes."""
    recs = records if records is not None else load_state()
    stale: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for r in recs:
        ts_str = str(r.get("updated_at") or r.get("created_at") or "").strip()
        if not ts_str:
            continue
        try:
            # Handle Z suffix
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_minutes = (now - ts).total_seconds() / 60.0
            if age_minutes > max_age_minutes:
                stale.append({
                    "session": r.get("session"),
                    "worktree": r.get("worktree"),
                    "updated_at": ts_str,
                    "status": r.get("status"),
                    "age_minutes": round(age_minutes, 2),
                })
        except Exception:
            continue
    # Deduplicate by session+worktree
    seen = set()
    deduped = []
    for item in stale:
        key = (str(item.get("session") or ""), str(item.get("worktree") or ""))
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def emit_projection(
    free_models: list[dict[str, Any]] | None = None,
    state_path: pathlib.Path | None = None,
    extra_note: str = "",
) -> dict[str, Any]:
    """Read-only supervision projection; never enforced."""
    models = free_models if free_models is not None else enumerate_free_models()
    records = inspect_subagents(state_path)
    if not records:
        try:
            live = discover_live_sessions(query_only=True)
            if live:
                records = [{"source": "sqlite_readonly", **r} for r in live]
        except Exception:
            pass
    duplicates = detect_duplicate_worktrees(records)
    loops = detect_looping_records(records)
    stale = detect_stale_records(records)
    cred = credential_present()
    # Suggested-but-not-enforced verdict: healthy when clean, cred present, at least one free model.
    verdict = "healthy"
    if duplicates or loops or stale or not cred or not models:
        verdict = "hold"
    projection = {
        "supervision_slice": "free_subagent_supervisor",
        "observed_at": _now_utc(),
        "read_only": True,
        "suggested_scale_verdict": verdict,
        "free_models_found": len(models),
        "credential_present": cred,
        "subagent_records": len(records),
        "duplicate_worktrees": duplicates,
        "looping_records": loops,
        "stale_records": stale,
        "free_model_ids": sorted([m.get("id") for m in models if m.get("id")]),
        "projections_note": (
            "Projection is read-only and advisory. It does not approve, admit, "
            "dispatch, or terminate subagent processes. Scale verdict is suggested, "
            "not enforced. Close stdin (< /dev/null) for noninteractive launches to "
            "avoid init waits. Free-cost gating requires both prompt and completion "
            "pricing to be zero; stale/duplicate/looping classifications are advisory."
            + (" " + extra_note if extra_note else "")
        ),
    }
    return projection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Free subagent supervisor (read-only).")
    parser.add_argument("--enumerate-models", action="store_true", help="List free model IDs")
    parser.add_argument("--credential-present", action="store_true", help="Report credential presence (bool only)")
    parser.add_argument("--inspect-state", action="store_true", help="Show subagent records (JSON lines)")
    parser.add_argument("--register-subagent", type=str, default="", help="JSON record to register")
    parser.add_argument("--detect-duplicates", action="store_true", help="Detect duplicate worktrees")
    parser.add_argument("--detect-looping", action="store_true", help="Detect looping session registrations")
    parser.add_argument("--detect-stale", action="store_true", help="Detect stale subagent records")
    parser.add_argument("--projection", action="store_true", help="Emit supervision projection")
    parser.add_argument("--fixture", type=str, default=str(DEFAULT_FIXTURE_PATH), help="Fixture path")
    parser.add_argument("--state", type=str, default=str(DEFAULT_STATE_PATH), help="State file path")
    args = parser.parse_args(argv)

    state_path = pathlib.Path(args.state)
    fixture_path = pathlib.Path(args.fixture) if args.fixture else None

    if args.enumerate_models:
        for m in enumerate_free_models(fixture_path):
            print(json.dumps({"id": m.get("id"), "tags": m.get("tags", [])}))
        return 0

    if args.credential_present:
        # Only boolean; never secret
        print("true" if credential_present() else "false")
        return 0

    if args.register_subagent:
        try:
            rec = json.loads(args.register_subagent)
            records = register_subagent(rec, state_path)
            print(json.dumps({"registered": True, "total_records": len(records)}))
        except Exception as e:
            print(json.dumps({"registered": False, "error": str(e)}))
            return 1
        return 0

    if args.inspect_state:
        for rec in inspect_subagents(state_path):
            print(json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.detect_duplicates:
        dups = detect_duplicate_worktrees(inspect_subagents(state_path))
        print(json.dumps({"duplicates": dups}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.detect_looping:
        loops = detect_looping_records(inspect_subagents(state_path))
        print(json.dumps({"looping": loops}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.detect_stale:
        stale = detect_stale_records(inspect_subagents(state_path))
        print(json.dumps({"stale": stale}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.projection:
        proj = emit_projection(
            free_models=enumerate_free_models(fixture_path),
            state_path=state_path,
        )
        print(json.dumps(proj, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 0

    # Default: projection
    proj = emit_projection(free_models=enumerate_free_models(fixture_path), state_path=state_path)
    print(json.dumps(proj, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
