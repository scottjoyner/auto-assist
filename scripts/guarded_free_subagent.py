#!/usr/bin/env python3
"""Single-slot, fail-closed admission and runtime guard for exact free subagents.

Only processes using this wrapper share its lock. It cannot police other clients
or guarantee an upstream provider quota. No automatic retries or paid fallback.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
import pwd
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

DEFAULT_ROOT = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local/state/auto-assist/guarded-free"
DEFAULT_PRESENCE = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local/state/auto-assist/provider-presence.json"
ACCOUNT_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
DEFAULT_DBS = (ACCOUNT_HOME / ".local/share/opencode/opencode.db",
               ACCOUNT_HOME / ".local/share/mcp/dc-isolated/.home/.local/share/opencode/opencode.db")

def atomic_json(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

def scan_trace(path: Path) -> dict:
    usage = {"input": 0, "output": 0, "reasoning": 0, "steps": 0, "tools": 0}
    errors = {}
    if not path.exists():
        return {"usage": usage, "errors": errors}
    for line in path.open(errors="replace"):
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        part = event.get("part") or {}
        if not isinstance(part, dict):
            part = {}
        if event.get("type") == "step_finish":
            usage["steps"] += 1
            tok = part.get("tokens") or {}
            if not isinstance(tok, dict):
                tok = {}
            for key in ("input", "output", "reasoning"):
                value = tok.get(key, 0)
                if type(value) is int and value > 0:
                    usage[key] += value
        if event.get("type") == "tool_use":
            usage["tools"] += 1
        if event.get("type") == "error":
            err = event.get("error") or {}
            data = (err.get("data") or {}) if isinstance(err, dict) else {}
            if not isinstance(data, dict):
                data = {}
            status = str(data.get("statusCode", "unknown"))
            errors[status] = errors.get(status, 0) + 1
    return {"usage": usage, "errors": errors}

def active_pid(state: Path, title: str) -> int | None:
    if not state.exists():
        return None
    for line in reversed(state.read_text().splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("title") != title or row.get("status") != "running":
            continue
        pid = row.get("opencode_pid")
        if type(pid) is not int or pid < 2:
            continue
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").lower()
        except OSError:
            continue
        if b"opencode" in cmdline:
            return pid
    return None

def provider_preflight(path: Path, *, now: float | None = None,
                       provider_id: str = "kilo_anonymous",
                       max_age: int = 3600) -> dict:
    """Fail closed on missing/stale/blocked provider metadata.

    Catalog health is a necessary admission signal, never proof of a usable
    model or of remaining free quota. Other harnesses must opt in separately.
    """
    now = time.time() if now is None else now
    try:
        stat = path.lstat()
        if not path.is_file() or path.is_symlink() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            return {"allowed": False, "reason": "untrusted_presence_state"}
        state = json.loads(path.read_text())
        if not isinstance(state, dict) or state.get("schema") != "provider-presence/v1":
            return {"allowed": False, "reason": "invalid_presence_state"}
        record = state.get("providers", {}).get(provider_id)
        if not isinstance(record, dict):
            return {"allowed": False, "reason": "provider_not_monitored"}
        checked = record.get("checked_epoch")
        if not isinstance(checked, (int, float)) or checked > now + 60 or checked < now - max_age:
            return {"allowed": False, "reason": "stale_presence"}
        status = record.get("status")
        if status != "catalog_reachable":
            return {"allowed": False, "reason": "provider_" + str(status or "unknown")}
        return {"allowed": True, "reason": "catalog_healthy_not_inference_proof"}
    except FileNotFoundError:
        return {"allowed": False, "reason": "missing_presence_state"}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"allowed": False, "reason": "invalid_presence_state"}


def recent_kilo_usage_preflight(databases=DEFAULT_DBS, *, now: float | None = None,
                                window_seconds: int = 900, max_input: int = 60000,
                                max_output: int = 6000, max_sessions: int = 3) -> dict:
    """Conservative local recent-activity cap across account and isolated DBs.

    Session lifetime usage is charged if the session was updated in the window,
    so this overestimates true per-window usage. It is NOT an upstream quota.
    """
    now = time.time() if now is None else now
    cutoff_ms = int((now - window_seconds) * 1000)
    by_id = {}
    for db in databases:
        if not db.is_file():
            return {"allowed": False, "reason": "usage_database_missing"}
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
            try:
                con.execute("PRAGMA query_only=ON")
                rows = con.execute(
                    "SELECT id,model,tokens_input,tokens_output,cost FROM session "
                    "WHERE time_updated>=? ORDER BY time_updated DESC LIMIT 2001",
                    (cutoff_ms,)
                ).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            return {"allowed": False, "reason": "usage_database_error"}
        if len(rows) > 2000:
            return {"allowed": False, "reason": "usage_window_truncated"}
        for sid, model_raw, input_tokens, output_tokens, cost in rows:
            try:
                model = json.loads(model_raw or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(model, dict) or model.get("providerID") not in ("kilo", "kilo_free"):
                continue
            previous = by_id.get(sid, (0, 0, 0))
            by_id[sid] = (
                max(previous[0], max(0, input_tokens or 0)),
                max(previous[1], max(0, output_tokens or 0)),
                max(previous[2], max(0, cost or 0)),
            )
    input_total = sum(x[0] for x in by_id.values())
    output_total = sum(x[1] for x in by_id.values())
    detail = {"sessions": len(by_id), "input_tokens": input_total,
              "output_tokens": output_total, "window_seconds": window_seconds}
    if any(x[2] > 0 for x in by_id.values()):
        return {"allowed": False, "reason": "recent_nonzero_provider_cost", **detail}
    if len(by_id) >= max_sessions or input_total >= max_input or output_total >= max_output:
        return {"allowed": False, "reason": "recent_provider_activity_cap", **detail}
    return {"allowed": True, "reason": "within_conservative_local_window", **detail}


def run(args: argparse.Namespace) -> dict:
    root = args.guard_root
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    lock = root / "kilo_free.lock"
    with lock.open("a+") as lock_fd:
        os.chmod(lock, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "blocked", "reason": "concurrent_guarded_run"}
        statefile = root / "kilo_free.json"
        try:
            prior = json.loads(statefile.read_text())
        except (FileNotFoundError, ValueError):
            prior = {}
        now = time.time()
        if now < prior.get("next_allowed_epoch", 0):
            return {"status": "blocked", "reason": "cooldown", "remaining_seconds": round(prior["next_allowed_epoch"] - now)}
        args.trace_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(args.trace_dir, 0o700)
        args.state.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(args.state.parent, 0o700)
        stdout_trace = args.trace_dir / f"{args.title}.stdout.jsonl"
        if stdout_trace.exists():
            return {"status": "blocked", "reason": "trace_name_already_exists"}
        admission = provider_preflight(getattr(args, "presence_state", DEFAULT_PRESENCE))
        if not admission["allowed"]:
            denial = {"status": "blocked", "reason": admission["reason"],
                      "provider": "kilo_anonymous", "title": args.title,
                      "checked_epoch": round(time.time(), 3)}
            atomic_json(root / f"{args.title}.admission.json", denial)
            return {"status": "blocked", "reason": admission["reason"]}
        budget = recent_kilo_usage_preflight()
        if not budget["allowed"]:
            denial = {"status": "blocked", "reason": budget["reason"],
                      "provider": "kilo_anonymous", "title": args.title,
                      "recent_usage": {k: v for k, v in budget.items() if k not in ("allowed", "reason")},
                      "checked_epoch": round(time.time(), 3)}
            atomic_json(root / f"{args.title}.admission.json", denial)
            return {"status": "blocked", "reason": budget["reason"]}
        command = [sys.executable, str(args.launcher), "--model", args.model,
                   "--objective", args.objective, "--worktree", str(args.worktree),
                   "--title", args.title, "--state", str(args.state),
                   "--trace-dir", str(args.trace_dir)]
        stdout = root / f"{args.title}.launcher.stdout.json"
        stderr = root / f"{args.title}.launcher.stderr.log"
        started = time.monotonic()
        reason = None
        with stdout.open("wb") as out, stderr.open("wb") as err:
            os.chmod(stdout, 0o600)
            os.chmod(stderr, 0o600)
            proc = subprocess.Popen(command, stdout=out, stderr=err,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
            while proc.poll() is None:
                evidence = scan_trace(stdout_trace)
                usage, errors = evidence["usage"], evidence["errors"]
                elapsed = time.monotonic() - started
                if "429" in errors or "503" in errors:
                    reason = "provider_overloaded"
                elif "402" in errors or "401" in errors:
                    reason = "provider_access_blocked"
                elif usage["input"] > args.max_input or usage["output"] > args.max_output:
                    reason = "token_budget_exceeded"
                elif usage["steps"] > args.max_steps or usage["tools"] > args.max_tools:
                    reason = "step_budget_exceeded"
                elif elapsed > args.max_seconds:
                    reason = "wall_time_exceeded"
                if reason:
                    child = active_pid(args.state, args.title)
                    try:
                        if child is not None:
                            os.kill(child, signal.SIGTERM)
                        else:
                            os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    break
                time.sleep(0.5)
            exit_code = proc.wait()
        evidence = scan_trace(stdout_trace)
        try:
            result = json.loads(stdout.read_text())
        except (OSError, ValueError):
            result = {}
        manifest = Path(result["manifest"]) if result.get("manifest") else None
        valid = bool(manifest and manifest.is_file())
        if valid:
            record = json.loads(manifest.read_text())
            digest = hashlib.sha256(stdout_trace.read_bytes()).hexdigest()
            valid = bool(record.get("stdout", {}).get("sha256") == digest
                         and record.get("model_identity_complete")
                         and record.get("cost") == 0
                         and record.get("status") == "completed")
        status = "completed" if valid and exit_code == 0 and not reason else "failed"
        errors = evidence["errors"]
        cooldown = args.cooldown
        if reason == "provider_overloaded" or "429" in errors or "503" in errors:
            cooldown = args.error_cooldown
        elif reason == "provider_access_blocked" or "402" in errors or "401" in errors:
            cooldown = args.access_cooldown
        atomic_json(statefile, {"last_status": status, "reason": reason,
                    "last_model": args.model, "next_allowed_epoch": time.time() + cooldown})
        report = {"status": status, "reason": reason or (None if valid else "invalid_or_missing_manifest"),
                  "model": args.model, "exit_code": exit_code,
                  "duration_seconds": round(time.monotonic() - started, 2),
                  "output_tokens_per_second": round(evidence["usage"]["output"] / max(time.monotonic()-started, .1), 2),
                  "evidence": evidence, "manifest": str(manifest) if manifest else None,
                  "manifest_verified": valid, "guard_state": str(statefile)}
        atomic_json(root / f"{args.title}.report.json", report)
        return report

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--launcher", type=Path, default=Path(__file__).with_name("launch_kilo_exact.py"))
    parser.add_argument("--guard-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--presence-state", type=Path, default=DEFAULT_PRESENCE)
    parser.add_argument("--max-input", type=int, default=60000)
    parser.add_argument("--max-output", type=int, default=4000)
    parser.add_argument("--max-steps", type=int, default=3)
    parser.add_argument("--max-tools", type=int, default=4)
    parser.add_argument("--max-seconds", type=int, default=90)
    parser.add_argument("--cooldown", type=int, default=180)
    parser.add_argument("--error-cooldown", type=int, default=3600)
    parser.add_argument("--access-cooldown", type=int, default=86400)
    args = parser.parse_args()
    report = run(args)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "completed" else 1

if __name__ == "__main__":
    raise SystemExit(main())
