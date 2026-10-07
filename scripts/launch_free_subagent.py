#!/usr/bin/env python3
"""Budget-aware launcher for attributed zero-cost subagents.

Selection is projection-driven and fail-closed:
- proven exact models are preferred,
- trial models require explicit opt-in,
- blocked/unavailable models are never launched,
- Z.AI remains reserve-only,
- the projection must be fresh and authoritative routing remains unchanged.

This wrapper delegates execution and trace capture to launch_kilo_exact.py.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import pwd
import time
from typing import Any


def _state_root() -> pathlib.Path:
    override = os.environ.get("ASSIST_SUBAGENT_STATE_ROOT")
    if override:
        return pathlib.Path(override).expanduser()
    home = pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir)
    return home / ".local" / "state" / "auto-assist" / "subagents"


def _load_launcher():
    path = pathlib.Path(__file__).with_name("launch_kilo_exact.py")
    spec = importlib.util.spec_from_file_location("launch_kilo_exact_selected", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUDGET_PROFILES: dict[str, dict[str, Any]] = {
    "proven": {
        "max_wall_seconds": 420.0,
        "max_input_tokens": 300_000,
        "max_reasoning_tokens": 100_000,
        "max_tool_calls": 60,
        "max_no_progress_input_tokens": 50_000,
        "budget_poll_seconds": 1.0,
    },
    "trial": {
        "max_wall_seconds": 180.0,
        "max_input_tokens": 100_000,
        "max_reasoning_tokens": 40_000,
        "max_tool_calls": 30,
        "max_no_progress_input_tokens": 25_000,
        "budget_poll_seconds": 1.0,
    },
}


def load_projection(
    path: pathlib.Path,
    *,
    max_age_seconds: float,
    now: float | None = None,
) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.exists():
        raise ValueError(f"free-pool projection is missing: {path}")
    now = time.time() if now is None else now
    age = max(0.0, now - path.stat().st_mtime)
    if max_age_seconds > 0 and age > max_age_seconds:
        raise ValueError(
            f"free-pool projection is stale: age={age:.1f}s "
            f"limit={max_age_seconds:.1f}s"
        )
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid free-pool projection: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("free-pool projection must be a JSON object")
    if payload.get("read_only") is not True:
        raise ValueError("projection is not marked read_only")
    if payload.get("authoritative_routing_unchanged") is not True:
        raise ValueError("projection changed authoritative routing boundary")
    if payload.get("zai_policy") != "reserve_only":
        raise ValueError("Z.AI must remain reserve_only in the free-pool projection")
    return payload


def select_model(
    projection: dict[str, Any],
    *,
    allow_trial: bool = False,
    preferred_model: str | None = None,
) -> dict[str, Any]:
    kilo = projection.get("kilo") or {}
    proven = [str(x) for x in kilo.get("proven_exact_models") or [] if str(x)]
    trial = [str(x) for x in kilo.get("trial_exact_models") or [] if str(x)]

    allowed: list[tuple[str, str]] = [(model, "proven") for model in proven]
    if allow_trial:
        allowed.extend((model, "trial") for model in trial)

    if preferred_model:
        for model, tier in allowed:
            if model == preferred_model:
                return {
                    "model": model,
                    "tier": tier,
                    "budget": dict(BUDGET_PROFILES[tier]),
                }
        raise ValueError(
            f"preferred model is not eligible under current policy: {preferred_model}"
        )

    if allowed:
        model, tier = allowed[0]
        return {
            "model": model,
            "tier": tier,
            "budget": dict(BUDGET_PROFILES[tier]),
        }

    if trial and not allow_trial:
        raise ValueError(
            "no proven model is currently eligible; trial models exist but "
            "--allow-trial was not supplied"
        )
    raise ValueError("no eligible exact zero-cost model is available")


def launch_selected(
    *,
    projection_path: pathlib.Path,
    objective: str,
    worktree: pathlib.Path,
    state_root: pathlib.Path,
    allow_trial: bool = False,
    preferred_model: str | None = None,
    max_projection_age_seconds: float = 600.0,
    title: str | None = None,
    dry_run: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    projection = load_projection(
        projection_path,
        max_age_seconds=max_projection_age_seconds,
        now=now,
    )
    selection = select_model(
        projection,
        allow_trial=allow_trial,
        preferred_model=preferred_model,
    )
    launcher = _load_launcher()
    budget = selection["budget"]
    result = launcher.launch(
        model_id=selection["model"],
        objective=objective,
        worktree=worktree,
        state_path=state_root / "live.jsonl",
        trace_dir=state_root / "traces",
        db_path=launcher.opencode_db_default(),
        supervisor_path=pathlib.Path(__file__).with_name("free_subagent_supervisor.py"),
        exporter_path=pathlib.Path(__file__).with_name("export_opencode_session_traces.py"),
        title=title,
        dry_run=dry_run,
        **budget,
    )
    result["selection_tier"] = selection["tier"]
    result["selection_budget"] = budget
    result["projection_path"] = str(projection_path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--objective", required=True)
    parser.add_argument("--worktree", type=pathlib.Path, required=True)
    parser.add_argument("--state-root", type=pathlib.Path, default=_state_root())
    parser.add_argument("--projection", type=pathlib.Path)
    parser.add_argument("--preferred-model")
    parser.add_argument("--allow-trial", action="store_true")
    parser.add_argument("--max-projection-age-seconds", type=float, default=600.0)
    parser.add_argument("--title")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    state_root = args.state_root.expanduser().resolve()
    projection = (
        args.projection.expanduser().resolve()
        if args.projection
        else state_root / "free-pool-projection.json"
    )
    result = launch_selected(
        projection_path=projection,
        objective=args.objective,
        worktree=args.worktree,
        state_root=state_root,
        allow_trial=args.allow_trial,
        preferred_model=args.preferred_model,
        max_projection_age_seconds=args.max_projection_age_seconds,
        title=args.title,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("success", result.get("dry_run", False)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
