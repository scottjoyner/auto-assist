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


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enumerate_free_models(fixture_path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    """Return free-model records (id, provider, tags)."""
    results: list[dict[str, Any]] = []
    # Try authoritative command first; if unavailable/offline, fall back.
    try:
        proc = subprocess.run(
            ["opencode", "models", "openrouter"],
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
                        if isinstance(obj, dict) and obj.get("id"):
                            if any(t == "free" or ":free" in str(obj.get("id", "")) for t in obj.get("tags", []) + [str(obj.get("pricing") or "")]):
                                results.append(obj)
                            elif ":free" in str(obj.get("id", "")):
                                results.append(obj)
                            else:
                                # include if pricing is zero-like; but only include if clearly free-ish
                                pricing = obj.get("pricing") or {}
                                prompt = pricing.get("prompt") if isinstance(pricing, dict) else None
                                if prompt == "0" or prompt == 0:
                                    results.append(obj)
                    except json.JSONDecodeError:
                        # plain id line with free marker
                        if ":free" in line or "free" in line.lower():
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
                    if isinstance(obj, dict) and obj.get("id"):
                        # Only include if clearly free:
                        id_str = str(obj.get("id") or "")
                        pricing = obj.get("pricing") or {}
                        is_free = False
                        if ":free" in id_str:
                            is_free = True
                        tags = obj.get("tags") or []
                        if isinstance(tags, list) and "free" in tags:
                            is_free = True
                        if isinstance(pricing, dict) and (pricing.get("prompt") == "0" or pricing.get("prompt") == 0):
                            is_free = True
                        if is_free:
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


def detect_duplicate_worktrees(records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Detect active records with conflicting worktrees."""
    recs = records if records is not None else load_state()
    active = [r for r in recs if str(r.get("status") or "").lower() in ("active", "running", "in_progress")]
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


def emit_projection(
    free_models: list[dict[str, Any]] | None = None,
    state_path: pathlib.Path | None = None,
    extra_note: str = "",
) -> dict[str, Any]:
    """Read-only supervision projection; never enforced."""
    models = free_models if free_models is not None else enumerate_free_models()
    records = inspect_subagents(state_path)
    duplicates = detect_duplicate_worktrees(records)
    cred = credential_present()
    # Suggested-but-not-enforced verdict: healthy when no duplicates, cred present, at least one free model.
    verdict = "healthy"
    if duplicates or not cred or not models:
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
        "free_model_ids": [m.get("id") for m in models],
        "projections_note": (
            "Projection is read-only and advisory. It does not approve, admit, "
            "dispatch, or terminate subagent processes. Scale verdict is suggested, "
            "not enforced. Close stdin (< /dev/null) for noninteractive launches to "
            "avoid init waits."
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
