#!/usr/bin/env python3
"""Launch an exact anonymous Kilo OpenCode run with attributable traces.

This helper is intentionally narrow:
- exact Kilo free model IDs only; router aliases are rejected
- no paid or Z.AI fallback
- stdin is closed for noninteractive OpenCode execution
- the real OpenCode session ID is discovered from its SQLite DB
- the OpenCode child PID, worktree, objective digest, tokens/cost/tools, and
  exact effective model are recorded through the existing supervisor/exporter
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import pwd
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from typing import Any
from datetime import UTC, datetime

PROVIDER_ID = "kilo_free"
QUALIFIED_EXACT_MODELS = frozenset(
    {
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "cohere/north-mini-code:free",
        "dots-studio/dots-3-note-preview:free",
        "poolside/laguna-s-2.1:free",
        "liquid/lfm-2.5-2.6b:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "nvidia/nemotron-3.5-lightning:free",
        "inclusionai/ling-3.1-flash",
    }
)
ROUTE_ALIAS_TOKENS = frozenset({"free", "auto", "router", "any", "best", "default"})

def _state_root() -> pathlib.Path:
    env_root = os.environ.get("ASSIST_SUBAGENT_STATE_ROOT")
    if env_root:
        return pathlib.Path(env_root).expanduser()
    account_home = pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir)
    return account_home / ".local" / "state" / "auto-assist" / "subagents"


def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_private_dir(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _secure_file(path: pathlib.Path) -> None:
    if path.exists():
        os.chmod(path, 0o600)


def _file_info(path: pathlib.Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    data = path.read_bytes()
    return {
        "path": str(path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _write_manifest(
    *,
    state_path: pathlib.Path,
    session_id: str,
    title: str,
    model_id: str,
    worktree: pathlib.Path,
    launch_git: dict[str, Any],
    launch_timestamp: str,
    stdout_path: pathlib.Path,
    stderr_path: pathlib.Path,
    final_trace: dict[str, Any] | None,
    status: str,
    failure_class: str | None,
) -> pathlib.Path:
    manifests_dir = state_path.parent / "manifests"
    _ensure_private_dir(manifests_dir)
    manifest_path = manifests_dir / f"{session_id}.json"
    manifest = {
        "schema": "subagent-run-manifest/v1",
        "session_id": session_id,
        "title": title,
        "provider": PROVIDER_ID,
        "requested_model": model_id,
        "resolved_model": final_trace.get("resolved_model") if final_trace else None,
        "effective_model": (
            final_trace.get("effective_model") if final_trace else model_id
        ),
        "model_attribution": (
            final_trace.get("model_attribution") if final_trace else "exact-request"
        ),
        "model_identity_complete": (
            final_trace.get("model_identity_complete") if final_trace else True
        ),
        "worktree": str(worktree),
        "launch_git": launch_git,
        "final_git": final_trace.get("git") if final_trace else _git_snapshot(worktree),
        "launch_timestamp": launch_timestamp,
        "finished_at": _now_utc(),
        "status": status,
        "failure_class": failure_class,
        "tokens": final_trace.get("tokens") if final_trace else None,
        "cost": final_trace.get("cost") if final_trace else None,
        "tools": final_trace.get("tools") if final_trace else None,
        "patch_parts": final_trace.get("patch_parts") if final_trace else None,
        "last_finish_reason": (
            final_trace.get("last_finish_reason") if final_trace else None
        ),
        "stdout": _file_info(stdout_path),
        "stderr": _file_info(stderr_path),
    }
    temp_path = manifest_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    _secure_file(temp_path)
    os.replace(temp_path, manifest_path)
    _secure_file(manifest_path)
    return manifest_path


def route_kind(model_id: str) -> str:
    mid = str(model_id or "").strip()
    leaf = mid.rsplit("/", 1)[-1].lower().split(":")[0]
    return "alias" if leaf in ROUTE_ALIAS_TOKENS else "exact"


def validate_model(model_id: str) -> None:
    if route_kind(model_id) != "exact":
        raise ValueError("router aliases are not allowed for real anonymous work")
    if model_id not in QUALIFIED_EXACT_MODELS:
        raise ValueError(
            f"{model_id!r} is not in the explicitly qualified anonymous Kilo model set"
        )


def opencode_binary() -> str:
    configured = os.environ.get("OPENCODE_BIN")
    if configured:
        return configured
    on_path = shutil.which("opencode")
    if on_path:
        return on_path
    candidates = [
        pathlib.Path.home() / ".opencode" / "bin" / "opencode",
        pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir) / ".opencode" / "bin" / "opencode",
    ]
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return "opencode"


def opencode_db_default() -> pathlib.Path:
    return pathlib.Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def build_command(
    model_id: str,
    title: str,
    objective: str,
    worktree: pathlib.Path,
) -> list[str]:
    validate_model(model_id)
    return [
        opencode_binary(),
        "run",
        "--auto",
        "--format",
        "json",
        "--title",
        title,
        "--dir",
        str(worktree.resolve()),
        "-m",
        f"{PROVIDER_ID}/{model_id}",
        objective,
    ]


def classify_failure(text: str) -> str:
    lowered = text.lower()
    if "429" in lowered or "rate limit" in lowered or "rate-limited" in lowered:
        return "rate_limited"
    if "503" in lowered or "temporarily overloaded" in lowered or "overloaded" in lowered:
        return "provider_overloaded"
    if "402" in lowered or "payment required" in lowered:
        return "payment_required"
    if "quota" in lowered and ("exhaust" in lowered or "limit" in lowered):
        return "quota_exhausted"
    if "401" in lowered or "unauthorized" in lowered or "authentication" in lowered:
        return "auth_error"
    return "process_error"


def _session_id_from_jsonl(stdout_path: pathlib.Path) -> str | None:
    """Read the OpenCode-emitted sessionID from its JSONL event stream."""
    if not stdout_path.exists():
        return None
    try:
        for line in stdout_path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = event.get("sessionID")
            if isinstance(session_id, str) and session_id:
                return session_id
    except OSError:
        return None
    return None


def wait_for_session(
    db_path: pathlib.Path,
    *,
    title: str,
    model_id: str,
    stdout_path: pathlib.Path,
    not_before_ms: int,
    timeout_seconds: float = 30.0,
) -> str | None:
    """Join an OpenCode process to its real SQLite session.

    Prefer the sessionID emitted by OpenCode's JSONL stream, then verify that
    ID against the read-only SQLite row and exact provider/model. Fall back to
    a unique recent title+model match. We intentionally do not require the DB
    directory to equal the launch cwd because OpenCode may record its project
    root rather than the process cwd.
    """
    deadline = time.monotonic() + timeout_seconds
    requested = model_id
    while time.monotonic() < deadline:
        emitted_session_id = _session_id_from_jsonl(stdout_path)
        if db_path.exists():
            try:
                con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    candidates: list[tuple[Any, ...]] = []
                    if emitted_session_id:
                        row = con.execute(
                            """
                            SELECT id, title, directory, model, time_created
                            FROM session
                            WHERE id = ?
                            LIMIT 1
                            """,
                            (emitted_session_id,),
                        ).fetchone()
                        if row:
                            candidates.append(row)
                    if not candidates:
                        candidates = con.execute(
                            """
                            SELECT id, title, directory, model, time_created
                            FROM session
                            WHERE title = ? AND time_created >= ?
                            ORDER BY time_created DESC
                            LIMIT 3
                            """,
                            (title, not_before_ms - 5_000),
                        ).fetchall()
                finally:
                    con.close()

                matches: list[str] = []
                for sid, row_title, _row_directory, raw_model, _created in candidates:
                    if row_title != title:
                        continue
                    try:
                        parsed = json.loads(raw_model) if isinstance(raw_model, str) else {}
                    except json.JSONDecodeError:
                        parsed = {}
                    provider = parsed.get("providerID")
                    row_model = parsed.get("id") or parsed.get("modelID")
                    if provider == PROVIDER_ID and row_model == requested:
                        matches.append(str(sid))
                if emitted_session_id and emitted_session_id in matches:
                    return emitted_session_id
                if len(matches) == 1:
                    return matches[0]
            except sqlite3.Error:
                pass
        time.sleep(0.10)
    return None


def _git_snapshot(directory: pathlib.Path) -> dict[str, Any]:
    """Capture launch-worktree Git provenance without mutating it."""
    try:
        root = subprocess.check_output(
            ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        branch = subprocess.check_output(
            ["git", "-C", str(directory), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        head = subprocess.check_output(
            ["git", "-C", str(directory), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        status = subprocess.check_output(
            ["git", "-C", str(directory), "status", "--porcelain"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return {
            "is_git": True,
            "root": root,
            "branch": branch,
            "head": head,
            "dirty_paths": len([line for line in status.splitlines() if line]),
        }
    except Exception:
        return {"is_git": False}


def _load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _final_session_trace(
    exporter_path: pathlib.Path,
    db_path: pathlib.Path,
    session_id: str,
) -> dict[str, Any] | None:
    exporter = _load_module(exporter_path, "opencode_trace_exporter")
    records = exporter.export_sessions(db_path, session_id=session_id)
    return records[0] if records else None


def _register_initial(
    supervisor_path: pathlib.Path,
    state_path: pathlib.Path,
    *,
    title: str,
    objective: str,
    model_id: str,
    worktree: pathlib.Path,
    opencode_pid: int,
    session_id: str,
) -> dict[str, Any]:
    supervisor = _load_module(supervisor_path, "free_subagent_supervisor_launch")
    launch_git = _git_snapshot(worktree)
    supervisor.register_subagent(
        {
            "title": title,
            "objective": objective,
            "objective_sha256": hashlib.sha256(objective.encode()).hexdigest(),
            "provider": PROVIDER_ID,
            "model": model_id,
            "worktree": str(worktree.resolve()),
            "launch_git": launch_git,
            "pid": opencode_pid,
            "opencode_pid": opencode_pid,
            "launcher_pid": os.getpid(),
            "session": session_id,
            "status": "running",
        },
        state_path,
    )
    return launch_git


def _update_state(
    supervisor_path: pathlib.Path,
    state_path: pathlib.Path,
    session_id: str,
    *,
    status: str,
    final_trace: dict[str, Any] | None,
    failure_class: str | None = None,
) -> None:
    supervisor = _load_module(supervisor_path, "free_subagent_supervisor_update")
    updates: dict[str, Any] = {"status": status}
    if failure_class:
        updates["failure_class"] = failure_class
    if final_trace:
        updates.update(
            {
                "tokens": final_trace.get("tokens"),
                "cost": final_trace.get("cost"),
                "tools": final_trace.get("tools"),
                "last_finish_reason": final_trace.get("last_finish_reason"),
                "assistant_error_count": final_trace.get("assistant_error_count"),
                "opencode_session_directory": final_trace.get("directory"),
                "opencode_session_git": final_trace.get("git"),
                "model_attribution": final_trace.get("model_attribution"),
                "effective_model": final_trace.get("effective_model"),
                "model_identity_complete": final_trace.get("model_identity_complete"),
            }
        )
    supervisor.update_subagent(session_id, updates, state_path)


def launch(
    *,
    model_id: str,
    objective: str,
    worktree: pathlib.Path,
    state_path: pathlib.Path,
    trace_dir: pathlib.Path,
    db_path: pathlib.Path,
    supervisor_path: pathlib.Path,
    exporter_path: pathlib.Path,
    title: str | None = None,
    session_timeout: float = 30.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    validate_model(model_id)
    worktree = worktree.resolve()
    if not worktree.is_dir():
        raise ValueError(f"worktree does not exist: {worktree}")

    title = title or f"kilo-exact-{model_id.split('/')[-1].replace(':', '-')}-{uuid.uuid4().hex[:8]}"
    cmd = build_command(model_id, title, objective, worktree)

    if dry_run:
        return {
            "dry_run": True,
            "provider": PROVIDER_ID,
            "requested_model": model_id,
            "effective_model": model_id,
            "model_attribution": "exact-request",
            "model_identity_complete": True,
            "worktree": str(worktree),
            "title": title,
            "command": cmd,
            "stdin": "/dev/null",
        }

    _ensure_private_dir(trace_dir)
    _ensure_private_dir(state_path.parent)
    stdout_path = trace_dir / f"{title}.stdout.jsonl"
    stderr_path = trace_dir / f"{title}.stderr.log"
    launch_timestamp = _now_utc()

    env = dict(os.environ)
    env.pop("OPENROUTER_API_KEY", None)
    env.setdefault("OPENCODE_DISABLE_MODELS_FETCH", "1")
    env.setdefault("OPENCODE_DISABLE_AUTOUPDATE", "1")
    env.setdefault("OPENCODE_DISABLE_PRUNE", "1")

    launch_started_ms = int(time.time() * 1000)
    with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
        _secure_file(stdout_path)
        _secure_file(stderr_path)
        proc = subprocess.Popen(
            cmd,
            cwd=worktree,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
        )

        session_id = wait_for_session(
            db_path,
            title=title,
            model_id=model_id,
            stdout_path=stdout_path,
            not_before_ms=launch_started_ms,
            timeout_seconds=session_timeout,
        )
        if not session_id:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            raise RuntimeError("OpenCode process started but no matching session appeared in SQLite")

        launch_git = _register_initial(
            supervisor_path,
            state_path,
            title=title,
            objective=objective,
            model_id=model_id,
            worktree=worktree,
            opencode_pid=proc.pid,
            session_id=session_id,
        )
        _secure_file(state_path)
        returncode = proc.wait()

    stderr_text = stderr_path.read_text(errors="replace")
    stdout_text = stdout_path.read_text(errors="replace")
    final_trace = _final_session_trace(exporter_path, db_path, session_id)
    failure_class = None if returncode == 0 else classify_failure(stderr_text + "\n" + stdout_text)
    status = "completed" if returncode == 0 else "failed"
    _update_state(
        supervisor_path,
        state_path,
        session_id,
        status=status,
        final_trace=final_trace,
        failure_class=failure_class,
    )
    _secure_file(state_path)
    manifest_path = _write_manifest(
        state_path=state_path,
        session_id=session_id,
        title=title,
        model_id=model_id,
        worktree=worktree,
        launch_git=launch_git,
        launch_timestamp=launch_timestamp,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        final_trace=final_trace,
        status=status,
        failure_class=failure_class,
    )

    return {
        "success": returncode == 0,
        "returncode": returncode,
        "provider": PROVIDER_ID,
        "requested_model": model_id,
        "effective_model": model_id,
        "model_attribution": "exact-request",
        "model_identity_complete": True,
        "session_id": session_id,
        "opencode_pid": proc.pid,
        "launcher_pid": os.getpid(),
        "worktree": str(worktree),
        "title": title,
        "state_path": str(state_path),
        "stdout_trace": str(stdout_path),
        "stderr_trace": str(stderr_path),
        "manifest": str(manifest_path),
        "failure_class": failure_class,
        "final_trace": final_trace,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=False)
    parser.add_argument("--objective")
    parser.add_argument("--worktree", type=pathlib.Path)
    parser.add_argument("--title")
    parser.add_argument("--state", type=pathlib.Path, default=_state_root() / "live.jsonl")
    parser.add_argument("--trace-dir", type=pathlib.Path, default=_state_root() / "traces")
    parser.add_argument("--opencode-db", type=pathlib.Path, default=opencode_db_default())
    parser.add_argument(
        "--supervisor",
        type=pathlib.Path,
        default=pathlib.Path(__file__).with_name("free_subagent_supervisor.py"),
    )
    parser.add_argument(
        "--trace-exporter",
        type=pathlib.Path,
        default=pathlib.Path(__file__).with_name("export_opencode_session_traces.py"),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-qualified", action="store_true")
    args = parser.parse_args(argv)

    if args.list_qualified:
        print(json.dumps(sorted(QUALIFIED_EXACT_MODELS)))
        return 0

    if not args.model or args.objective is None or args.worktree is None:
        parser.error("--model, --objective, and --worktree are required unless --list-qualified is used")

    result = launch(
        model_id=args.model,
        objective=args.objective,
        worktree=args.worktree,
        state_path=args.state,
        trace_dir=args.trace_dir,
        db_path=args.opencode_db,
        supervisor_path=args.supervisor,
        exporter_path=args.trace_exporter,
        title=args.title,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("success", result.get("dry_run", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
