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
import subprocess
import sys
import time

DEFAULT_ROOT = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local/state/auto-assist/guarded-free"

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
