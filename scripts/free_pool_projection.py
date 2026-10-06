#!/usr/bin/env python3
"""Read-only free-pool operational projection.

Combines:
- live Kilo anonymous zero-cost exact-model discovery,
- persisted qualification evidence,
- central subagent lifecycle/trace evidence,
- model task-fitness,
- independent provider credential/capacity metadata.

This script does not launch models, mutate routing authority, spend paid quota,
or read/emit credential values.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
from datetime import UTC, datetime
from typing import Any, Iterable

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_STATE_ROOT = (
    pathlib.Path.home() / ".local" / "state" / "auto-assist" / "subagents"
)


def _load_module(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


supervisor = _load_module(
    REPO_ROOT / "scripts" / "free_subagent_supervisor.py",
    "free_pool_projection_supervisor",
)
capacity = _load_module(
    REPO_ROOT / "src" / "assistx" / "free_provider_capacity.py",
    "free_pool_projection_capacity",
)
fitness = _load_module(
    REPO_ROOT / "src" / "assistx" / "model_fitness.py",
    "free_pool_projection_fitness",
)


FAILURE_STATES = frozenset(
    {
        "rate_limited",
        "provider_overloaded",
        "quota_exhausted",
        "payment_required",
        "auth_error",
        "control_plane_timeout",
    }
)


def _parse_time(value: Any) -> float:
    if isinstance(value, (int, float)):
        # OpenCode milliseconds are common.
        return float(value) / 1000.0 if value > 10_000_000_000 else float(value)
    if not isinstance(value, str) or not value.strip():
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    except ValueError:
        return 0.0


def _read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def load_state_records(state_root: pathlib.Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for name in ("history.jsonl", "live.jsonl"):
        records.extend(_read_jsonl(state_root / name))
    # Deduplicate by session, preferring the row with the newest lifecycle time.
    by_session: dict[str, dict[str, Any]] = {}
    unkeyed: list[dict[str, Any]] = []
    for record in records:
        sid = str(record.get("session") or record.get("session_id") or "").strip()
        if not sid:
            unkeyed.append(record)
            continue
        stamp = max(
            _parse_time(record.get("ended_at")),
            _parse_time(record.get("updated_at")),
            _parse_time(record.get("created_at")),
        )
        prior = by_session.get(sid)
        prior_stamp = -1.0
        if prior is not None:
            prior_stamp = max(
                _parse_time(prior.get("ended_at")),
                _parse_time(prior.get("updated_at")),
                _parse_time(prior.get("created_at")),
            )
        if prior is None or stamp >= prior_stamp:
            by_session[sid] = record
    return unkeyed + list(by_session.values())


def load_qualification_records(state_root: pathlib.Path) -> list[dict[str, Any]]:
    return _read_jsonl(state_root / "provider-qualification.jsonl")


def _model_id(record: dict[str, Any]) -> str:
    return str(
        record.get("effective_model")
        or record.get("response_model")
        or record.get("requested_model")
        or record.get("model")
        or ""
    ).strip()


def _pid_matches_record(record: dict[str, Any]) -> bool:
    """Best-effort local liveness check for a recorded OpenCode process."""
    try:
        pid = int(record.get("opencode_pid") or record.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    cmdline_path = pathlib.Path(f"/proc/{pid}/cmdline")
    try:
        cmdline = cmdline_path.read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return False
    if "opencode" not in cmdline:
        return False
    title = str(record.get("title") or "").strip()
    return not title or title in cmdline


def _event_state(
    record: dict[str, Any],
    *,
    process_alive=_pid_matches_record,
) -> str | None:
    if str(record.get("status") or "").lower() == "running":
        return "busy" if process_alive(record) else None

    failure = str(record.get("failure_class") or "").strip()
    if failure in FAILURE_STATES:
        return failure

    http_status = record.get("http_status")
    if http_status == 429:
        return "rate_limited"
    if http_status == 503:
        return "provider_overloaded"
    if http_status == 402:
        return "payment_required"
    if http_status in {401, 403}:
        return "auth_error"

    if record.get("qualified") is True:
        return "usable"
    if str(record.get("status") or "").lower() == "completed":
        return "usable"
    return None


def _event_timestamp(record: dict[str, Any]) -> float:
    return max(
        _parse_time(record.get("finished_at")),
        _parse_time(record.get("ended_at")),
        _parse_time(record.get("updated_at")),
        _parse_time(record.get("started_at")),
        _parse_time(record.get("created_at")),
        _parse_time(record.get("time_updated")),
    )


def _capability_qualified(
    model_id: str,
    qualification_records: Iterable[dict[str, Any]],
) -> bool:
    for record in qualification_records:
        if str(record.get("provider") or "") != "kilo_free":
            continue
        if str(record.get("requested_model") or "") != model_id:
            continue
        if not record.get("qualified"):
            continue
        if not record.get("structured_tool_call"):
            continue
        if record.get("model_identity_complete") is not True:
            continue
        if str(record.get("response_model") or "") != model_id:
            continue
        try:
            if float(record.get("cost") or 0.0) != 0.0:
                continue
            upstream = record.get("upstream_inference_cost")
            if upstream is not None and float(upstream) != 0.0:
                continue
        except (TypeError, ValueError):
            continue
        return True
    return False


def _latest_model_state(
    model_id: str,
    qualification_records: Iterable[dict[str, Any]],
    state_records: Iterable[dict[str, Any]],
    *,
    process_alive=_pid_matches_record,
) -> tuple[str | None, float]:
    events: list[tuple[float, str]] = []
    for record in qualification_records:
        if str(record.get("provider") or "") != "kilo_free":
            continue
        if str(record.get("requested_model") or "") != model_id:
            continue
        state = _event_state(record, process_alive=process_alive)
        if state:
            events.append((_event_timestamp(record), state))

    for record in state_records:
        if str(record.get("provider") or "") != "kilo_free":
            continue
        if _model_id(record) != model_id:
            continue
        state = _event_state(record, process_alive=process_alive)
        if state:
            events.append((_event_timestamp(record), state))

    if not events:
        return None, 0.0
    events.sort(key=lambda item: item[0])
    return events[-1][1], events[-1][0]


def _task_family(record: dict[str, Any]) -> str:
    explicit = str(record.get("task_family") or "").strip()
    if explicit:
        return explicit
    title = str(record.get("title") or "").lower()
    objective = str(record.get("objective") or "").lower()
    if "canary" in title or "canary" in objective:
        return "canary"
    if "review" in title or "read-only review" in objective:
        return "review"
    if "test" in title and "implement" not in objective:
        return "test"
    return "coding"


def _fitness_record(record: dict[str, Any]):
    provider = str(record.get("provider") or "")
    requested = str(record.get("requested_model") or record.get("model") or "")
    effective = str(record.get("effective_model") or "") or None
    launch_git = record.get("launch_git") or {}
    final_git = record.get("final_git") or record.get("opencode_session_git") or {}
    launch_dirty = int(launch_git.get("dirty_paths") or 0) if isinstance(launch_git, dict) else 0
    final_dirty = int(final_git.get("dirty_paths") or 0) if isinstance(final_git, dict) else 0
    return fitness.SubagentTraceRecord(
        provider=provider,
        requested_model=requested,
        effective_model=effective,
        model_identity_complete=bool(record.get("model_identity_complete")),
        route_kind=str(record.get("route_kind") or ""),
        terminal_status=str(record.get("status") or ""),
        task_family=_task_family(record),
        failure_class=str(record.get("failure_class") or "") or None,
        last_finish_reason=str(record.get("last_finish_reason") or "") or None,
        assistant_error_count=int(record.get("assistant_error_count") or 0),
        tool_counts=dict(record.get("tools") or {}),
        patch_parts=int(record.get("patch_parts") or 0),
        launch_head=launch_git.get("head") if isinstance(launch_git, dict) else None,
        final_git_head=final_git.get("head") if isinstance(final_git, dict) else None,
        dirty_path_evidence=final_dirty > launch_dirty,
        token_counts=dict(record.get("tokens") or {}),
        tests_passed=record.get("tests_passed"),
    )


def model_fitness_projection(
    state_records: Iterable[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    converted = []
    for record in state_records:
        try:
            converted.append(_fitness_record(record))
        except (TypeError, ValueError):
            continue
    return fitness.aggregate_by_model_task(converted)


def _credential_presence() -> dict[str, bool]:
    # Environment only: names/booleans, never values. Other credential stores
    # can feed this projection explicitly later without exposing secrets.
    names = {
        name
        for spec in capacity.PROVIDER_SPECS.values()
        for name in spec.credential_vars
    }
    return {name: bool(os.environ.get(name)) for name in names}


def build_projection(
    *,
    catalog_records: list[dict[str, Any]],
    qualification_records: list[dict[str, Any]],
    state_records: list[dict[str, Any]],
    credential_presence: dict[str, bool] | None = None,
    process_alive=_pid_matches_record,
) -> dict[str, Any]:
    normalized = supervisor.normalize_kilo_catalog(catalog_records)
    catalog_ids = {str(row.get("id") or "") for row in normalized}
    fitness_by_group = model_fitness_projection(state_records)

    models: list[dict[str, Any]] = []
    for row in normalized:
        model_id = str(row.get("id") or "")
        supports_tools = "tools" in set(row.get("supported_parameters") or [])
        qualified = _capability_qualified(model_id, qualification_records)
        latest_state, latest_state_ts = _latest_model_state(
            model_id,
            qualification_records,
            state_records,
            process_alive=process_alive,
        )
        state = latest_state
        if state is None:
            state = "unqualified" if not qualified else "usable"
        elif state == "usable" and not qualified:
            # A completed OpenCode run is useful runtime evidence, but it does
            # not replace the strict provider-level structured-tool gate.
            state = "unqualified"

        coding_fitness = fitness_by_group.get(
            ("kilo_free", model_id, "coding")
        )
        models.append(
            {
                "id": model_id,
                "context_length": row.get("context_length"),
                "supports_tools": supports_tools,
                "supported_parameters": row.get("supported_parameters") or [],
                "capability_qualified": qualified,
                "capacity_state": state,
                "capacity_observed_at_epoch": latest_state_ts or None,
                "coding_fitness": coding_fitness,
                "eligible_for_recommendation": bool(
                    supports_tools
                    and qualified
                    and state == "usable"
                ),
            }
        )

    # Historical qualified identities that disappeared from the live catalog
    # remain visible as retired rather than being rewritten/dropped.
    historical_ids = {
        str(record.get("requested_model") or "")
        for record in qualification_records
        if record.get("qualified") is True
        and str(record.get("provider") or "") == "kilo_free"
    }
    retired = sorted(mid for mid in historical_ids if mid and mid not in catalog_ids)

    recommended = [
        model
        for model in models
        if model["eligible_for_recommendation"]
    ]
    recommended.sort(
        key=lambda model: (
            -float(
                ((model.get("coding_fitness") or {}).get("fitness_score") or 50.0)
            ),
            -int(model.get("context_length") or 0),
            model["id"],
        )
    )

    presence = credential_presence or _credential_presence()
    qualified_provider_ids = {"kilo_anonymous"} if recommended else set()
    observed_provider_states = {
        "kilo_anonymous": "usable" if recommended else "unqualified"
    }
    provider_view = capacity.provider_inventory(
        credential_presence=presence,
        qualified_provider_ids=qualified_provider_ids,
        observed_states=observed_provider_states,
    )

    return {
        "schema": "free-pool-projection/v1",
        "read_only": True,
        "authoritative_routing_unchanged": True,
        "zai_policy": "reserve_only",
        "kilo": {
            "catalog_exact_zero_cost_count": len(models),
            "models": models,
            "retired_catalog_missing": retired,
            "recommended_exact_models": [model["id"] for model in recommended],
        },
        "providers": provider_view,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=pathlib.Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--catalog-fixture", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args(argv)

    if args.catalog_fixture:
        payload = json.loads(args.catalog_fixture.read_text())
        catalog_records = payload.get("data", payload)
        if not isinstance(catalog_records, list):
            raise ValueError("catalog fixture must contain a list or {data:[...]}")
    else:
        catalog_records = supervisor.fetch_kilo_catalog()

    projection = build_projection(
        catalog_records=catalog_records,
        qualification_records=load_qualification_records(args.state_root),
        state_records=load_state_records(args.state_root),
    )
    text = json.dumps(projection, sort_keys=True, separators=(",", ":"))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        args.output.write_text(text + "\n")
        os.chmod(args.output, 0o600)
        state_root = args.state_root.expanduser().resolve()
        try:
            parent = args.output.parent.expanduser().resolve()
            if parent == state_root or state_root in parent.parents:
                os.chmod(args.output.parent, 0o700)
        except OSError:
            pass
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
