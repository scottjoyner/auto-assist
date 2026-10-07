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
QUALIFICATION_MAX_AGE_SECONDS = int(
    os.environ.get("ASSIST_KILO_QUALIFICATION_MAX_AGE_SECONDS", "86400")
)

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
    budget_limits: dict[str, Any] | None = None,
    budget_result: dict[str, Any] | None = None,
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
        "budget_limits": budget_limits,
        "budget_result": budget_result,
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


def _qualification_ledger_path(state_path: pathlib.Path | None = None) -> pathlib.Path:
    state = state_path or (_state_root() / "live.jsonl")
    return state.parent / "provider-qualification.jsonl"


def _read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    except OSError:
        return []
    return rows


def _started_at_epoch(record: dict[str, Any]) -> float | None:
    value = record.get("started_at")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def latest_qualification(
    model_id: str,
    state_path: pathlib.Path | None = None,
) -> dict[str, Any] | None:
    matches = [
        row
        for row in _read_jsonl(_qualification_ledger_path(state_path))
        if row.get("provider") == PROVIDER_ID
        and row.get("requested_model") == model_id
    ]
    if not matches:
        return None
    matches.sort(key=lambda row: _started_at_epoch(row) or 0.0)
    return matches[-1]


def qualification_allows_launch(
    model_id: str,
    state_path: pathlib.Path | None = None,
    *,
    now_epoch: float | None = None,
) -> tuple[bool, str]:
    if route_kind(model_id) != "exact":
        return False, "router_alias"

    record = latest_qualification(model_id, state_path)
    if record is None:
        return False, "missing_qualification"

    started = _started_at_epoch(record)
    now_epoch = time.time() if now_epoch is None else now_epoch
    if started is None or now_epoch - started > QUALIFICATION_MAX_AGE_SECONDS:
        return False, "stale_qualification"

    if record.get("qualified") is not True:
        return False, "not_qualified"
    if record.get("model_identity_complete") is not True:
        return False, "incomplete_model_identity"
    if record.get("capacity_state") != "usable":
        return False, f"capacity_{record.get('capacity_state') or 'unknown'}"
    if record.get("requested_model") != model_id:
        return False, "requested_model_mismatch"
    if record.get("response_model") != model_id:
        return False, "response_model_mismatch"
    if record.get("structured_tool_call") is not True:
        return False, "tool_canary_missing"

    for key in ("cost", "upstream_inference_cost"):
        value = record.get(key)
        if value is None:
            return False, f"{key}_unknown"
        try:
            if float(value) != 0.0:
                return False, f"{key}_nonzero"
        except (TypeError, ValueError):
            return False, f"{key}_invalid"

    return True, "qualified_exact_zero_cost"


def qualified_models_from_ledger(
    state_path: pathlib.Path | None = None,
    *,
    now_epoch: float | None = None,
) -> list[str]:
    ledger = _read_jsonl(_qualification_ledger_path(state_path))
    models = sorted(
        {
            str(row.get("requested_model"))
            for row in ledger
            if row.get("provider") == PROVIDER_ID
            and isinstance(row.get("requested_model"), str)
        }
    )
    return [
        model
        for model in models
        if qualification_allows_launch(
            model,
            state_path,
            now_epoch=now_epoch,
        )[0]
    ]


def validate_model(
    model_id: str,
    state_path: pathlib.Path | None = None,
) -> None:
    if route_kind(model_id) != "exact":
        raise ValueError("router aliases are not allowed for real anonymous work")
    allowed, reason = qualification_allows_launch(model_id, state_path)
    if not allowed:
        raise ValueError(
            f"{model_id!r} lacks current attributable zero-cost qualification: {reason}"
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
    state_path: pathlib.Path | None = None,
) -> list[str]:
    validate_model(model_id, state_path)
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
    expected_directory: pathlib.Path,
    stdout_path: pathlib.Path,
    not_before_ms: int,
    timeout_seconds: float = 30.0,
) -> str | None:
    """Join an OpenCode process to its real SQLite session.

    Prefer the sessionID emitted by OpenCode's JSONL stream, then verify that
    ID against the read-only SQLite row, exact provider/model, and assigned
    worktree directory. A directory mismatch is a containment failure: the
    run must not be trusted even when its model identity is otherwise exact.
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
                expected = expected_directory.resolve()
                for sid, row_title, row_directory, raw_model, _created in candidates:
                    if row_title != title:
                        continue
                    try:
                        parsed = json.loads(raw_model) if isinstance(raw_model, str) else {}
                    except json.JSONDecodeError:
                        parsed = {}
                    provider = parsed.get("providerID")
                    row_model = parsed.get("id") or parsed.get("modelID")
                    if provider != PROVIDER_ID or row_model != requested:
                        continue

                    recorded = pathlib.Path(str(row_directory or "")).expanduser().resolve()
                    if recorded != expected:
                        if emitted_session_id and str(sid) == emitted_session_id:
                            raise RuntimeError(
                                "OpenCode session directory mismatch: "
                                f"expected {expected}, recorded {recorded}"
                            )
                        continue
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
    """Read one final session trace without assuming exporter filter support."""
    exporter = _load_module(exporter_path, "opencode_trace_exporter")
    records = exporter.export_sessions(db_path)
    return next(
        (record for record in records if record.get("session_id") == session_id),
        None,
    )


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
    extra_updates: dict[str, Any] | None = None,
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
    if extra_updates:
        updates.update(extra_updates)
    supervisor.update_subagent(session_id, updates, state_path)


MUTATION_TOOLS = frozenset(
    {"edit", "write", "patch", "apply_patch", "multiedit", "create_file"}
)


def _session_budget_metrics(
    db_path: pathlib.Path,
    session_id: str,
) -> dict[str, Any]:
    """Read lightweight token/tool progress for one OpenCode session."""
    metrics: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "tool_calls": 0,
        "mutation_tool_calls": 0,
        "patch_parts": 0,
    }
    if not db_path.exists():
        return metrics
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = con.execute(
                """
                SELECT tokens_input, tokens_output, tokens_reasoning, tokens_cache_read
                FROM session WHERE id = ? LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if row:
                metrics.update(
                    {
                        "input_tokens": int(row[0] or 0),
                        "output_tokens": int(row[1] or 0),
                        "reasoning_tokens": int(row[2] or 0),
                        "cache_read_tokens": int(row[3] or 0),
                    }
                )
            tool_calls = 0
            mutation_calls = 0
            patch_parts = 0
            for (raw,) in con.execute(
                "SELECT data FROM part WHERE session_id = ?",
                (session_id,),
            ):
                try:
                    part = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                typ = part.get("type")
                if typ == "tool":
                    tool_calls += 1
                    name = str(part.get("tool") or part.get("name") or "").lower()
                    if name in MUTATION_TOOLS:
                        mutation_calls += 1
                elif typ == "patch":
                    patch_parts += 1
            metrics.update(
                {
                    "tool_calls": tool_calls,
                    "mutation_tool_calls": mutation_calls,
                    "patch_parts": patch_parts,
                }
            )
        finally:
            con.close()
    except sqlite3.Error:
        pass
    return metrics


def _has_worktree_progress(
    worktree: pathlib.Path,
    launch_git: dict[str, Any],
    metrics: dict[str, Any],
) -> bool:
    if metrics.get("mutation_tool_calls", 0) or metrics.get("patch_parts", 0):
        return True
    if not launch_git.get("is_git"):
        return False
    current = _git_snapshot(worktree)
    if not current.get("is_git"):
        return False
    return bool(
        current.get("head") != launch_git.get("head")
        or int(current.get("dirty_paths") or 0) > int(launch_git.get("dirty_paths") or 0)
    )


def _stop_process(proc: subprocess.Popen[Any]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _wait_with_budgets(
    proc: subprocess.Popen[Any],
    *,
    db_path: pathlib.Path,
    session_id: str,
    worktree: pathlib.Path,
    launch_git: dict[str, Any],
    max_wall_seconds: float,
    max_input_tokens: int,
    max_reasoning_tokens: int,
    max_tool_calls: int,
    max_no_progress_input_tokens: int,
    poll_seconds: float = 1.0,
) -> tuple[int, str | None, dict[str, Any]]:
    """Wait for a run while enforcing deterministic resource budgets."""
    started = time.monotonic()
    last_metrics = _session_budget_metrics(db_path, session_id)
    while proc.poll() is None:
        elapsed = time.monotonic() - started
        last_metrics = _session_budget_metrics(db_path, session_id)
        progress = _has_worktree_progress(worktree, launch_git, last_metrics)
        last_metrics["worktree_progress"] = progress
        last_metrics["elapsed_seconds"] = round(elapsed, 3)

        reason: str | None = None
        if (
            max_no_progress_input_tokens > 0
            and last_metrics["input_tokens"] >= max_no_progress_input_tokens
            and not progress
        ):
            reason = "budget_no_progress"
        elif max_input_tokens > 0 and last_metrics["input_tokens"] >= max_input_tokens:
            reason = "budget_input_tokens"
        elif (
            max_reasoning_tokens > 0
            and last_metrics["reasoning_tokens"] >= max_reasoning_tokens
        ):
            reason = "budget_reasoning_tokens"
        elif max_tool_calls > 0 and last_metrics["tool_calls"] >= max_tool_calls:
            reason = "budget_tool_calls"
        elif max_wall_seconds > 0 and elapsed >= max_wall_seconds:
            reason = "budget_wall_clock"

        if reason:
            _stop_process(proc)
            return int(proc.returncode or -15), reason, last_metrics

        time.sleep(max(0.1, poll_seconds))

    last_metrics = _session_budget_metrics(db_path, session_id)
    last_metrics["worktree_progress"] = _has_worktree_progress(
        worktree, launch_git, last_metrics
    )
    last_metrics["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return int(proc.returncode or 0), None, last_metrics


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
    max_wall_seconds: float = 600.0,
    max_input_tokens: int = 500_000,
    max_reasoning_tokens: int = 200_000,
    max_tool_calls: int = 80,
    max_no_progress_input_tokens: int = 75_000,
    budget_poll_seconds: float = 1.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    validate_model(model_id, state_path)
    worktree = worktree.resolve()
    if not worktree.is_dir():
        raise ValueError(f"worktree does not exist: {worktree}")

    title = title or f"kilo-exact-{model_id.split('/')[-1].replace(':', '-')}-{uuid.uuid4().hex[:8]}"
    cmd = build_command(model_id, title, objective, worktree, state_path)
    budget_limits = {
        "max_wall_seconds": max_wall_seconds,
        "max_input_tokens": max_input_tokens,
        "max_reasoning_tokens": max_reasoning_tokens,
        "max_tool_calls": max_tool_calls,
        "max_no_progress_input_tokens": max_no_progress_input_tokens,
        "poll_seconds": budget_poll_seconds,
    }

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
            "budget_limits": budget_limits,
        }

    _ensure_private_dir(trace_dir)
    _ensure_private_dir(state_path.parent)
    stdout_path = trace_dir / f"{title}.stdout.jsonl"
    stderr_path = trace_dir / f"{title}.stderr.log"
    launch_timestamp = _now_utc()

    env = dict(os.environ)
    env.pop("OPENROUTER_API_KEY", None)
    env["PWD"] = str(worktree)
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

        try:
            session_id = wait_for_session(
                db_path,
                title=title,
                model_id=model_id,
                expected_directory=worktree,
                stdout_path=stdout_path,
                not_before_ms=launch_started_ms,
                timeout_seconds=session_timeout,
            )
        except Exception:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            raise

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
        returncode, budget_reason, budget_metrics = _wait_with_budgets(
            proc,
            db_path=db_path,
            session_id=session_id,
            worktree=worktree,
            launch_git=launch_git,
            max_wall_seconds=max_wall_seconds,
            max_input_tokens=max_input_tokens,
            max_reasoning_tokens=max_reasoning_tokens,
            max_tool_calls=max_tool_calls,
            max_no_progress_input_tokens=max_no_progress_input_tokens,
            poll_seconds=budget_poll_seconds,
        )

    stderr_text = stderr_path.read_text(errors="replace")
    stdout_text = stdout_path.read_text(errors="replace")
    final_trace = _final_session_trace(exporter_path, db_path, session_id)
    if budget_reason == "budget_no_progress":
        status = "no_progress"
        failure_class = budget_reason
    elif budget_reason:
        status = "budget_exhausted"
        failure_class = budget_reason
    else:
        failure_class = (
            None
            if returncode == 0
            else classify_failure(stderr_text + "\n" + stdout_text)
        )
        status = "completed" if returncode == 0 else "failed"
    budget_result = {
        "termination_reason": budget_reason,
        "metrics": budget_metrics,
    }
    _update_state(
        supervisor_path,
        state_path,
        session_id,
        status=status,
        final_trace=final_trace,
        failure_class=failure_class,
        extra_updates={
            "budget_limits": budget_limits,
            "budget_result": budget_result,
        },
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
        budget_limits=budget_limits,
        budget_result=budget_result,
    )

    return {
        "success": status == "completed",
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
        "budget_limits": budget_limits,
        "budget_result": budget_result,
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
    parser.add_argument("--max-wall-seconds", type=float, default=600.0)
    parser.add_argument("--max-input-tokens", type=int, default=500_000)
    parser.add_argument("--max-reasoning-tokens", type=int, default=200_000)
    parser.add_argument("--max-tool-calls", type=int, default=80)
    parser.add_argument("--max-no-progress-input-tokens", type=int, default=75_000)
    parser.add_argument("--budget-poll-seconds", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-qualified", action="store_true")
    args = parser.parse_args(argv)

    if args.list_qualified:
        print(json.dumps(qualified_models_from_ledger(args.state)))
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
        max_wall_seconds=args.max_wall_seconds,
        max_input_tokens=args.max_input_tokens,
        max_reasoning_tokens=args.max_reasoning_tokens,
        max_tool_calls=args.max_tool_calls,
        max_no_progress_input_tokens=args.max_no_progress_input_tokens,
        budget_poll_seconds=args.budget_poll_seconds,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("success", result.get("dry_run", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
