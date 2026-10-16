"""Free subagent supervision slice — supervision projection."""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

from free_subagent_routing import (
    ANONYMOUS_POOLS,
    KILO_ANONYMOUS_BASE_URL,
    POOL_PREFERENCE_ORDER,
    RESERVE_ONLY_POOLS,
    _FREE_PREFIX_PROVIDERS,
    _is_clearly_free_model,
    _least_severe_state,
    classify_free_model,
    enumerate_free_models,
    order_free_pools,
    pool_preference_rank,
    provider_state,
    qualify_provider_pools,
    route_kind,
)
from free_subagent_discover import (
    _annotate_trace_session,
    detect_duplicate_worktrees,
    detect_looping_records,
    detect_stale_records,
    discover_live_sessions,
)
from free_subagent_state import (
    DEFAULT_FIXTURE_PATH,
    credential_present,
    inspect_subagents,
    load_state,
)

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


def emit_projection(
    free_models: list[dict[str, Any]] | None = None,
    state_path: pathlib.Path | None = None,
    trace_exporter_path: pathlib.Path | None = None,
    extra_note: str = "",
    allow_reserve: bool = False,
    discover_fn=None,
) -> dict[str, Any]:
    models = free_models if free_models is not None else enumerate_free_models()

    model_types: dict[str, int] = {}
    for m in models:
        mtype = m.get("free_model_type", classify_free_model(m))
        model_types[mtype] = model_types.get(mtype, 0) + 1

    provider_pools = qualify_provider_pools(models, allow_reserve=allow_reserve)
    preferred_pool_entry = next(
        (p for p in provider_pools if p.get("state") == "usable" and not p.get("reserve_only")),
        None,
    )
    preferred_pool = preferred_pool_entry.get("pool") if preferred_pool_entry else None
    reserve_only_pools = [
        {"pool": p["pool"], "policy": p.get("policy", "reserve-only")}
        for p in provider_pools
        if p.get("reserve_only")
    ]
    anonymous_pools_usable = [
        p["pool"] for p in provider_pools if p.get("anonymous") and p.get("state") == "usable"
    ]
    route_kind_counts: dict[str, int] = {}
    for m in models:
        kind = route_kind(m.get("id") or "")
        route_kind_counts[kind] = route_kind_counts.get(kind, 0) + 1

    records = inspect_subagents(state_path)
    session_discovery_error: str | None = None
    if not records:
        try:
            if discover_fn is None:
                _sup = sys.modules.get("free_subagent_supervisor")
                if _sup is not None and hasattr(_sup, "discover_live_sessions"):
                    discover_fn = _sup.discover_live_sessions
                else:
                    from free_subagent_discover import discover_live_sessions as _discover
                    discover_fn = _discover
            live = discover_fn(query_only=True)
            if live:
                records = [{"source": "sqlite_readonly", **r} for r in live]
        except Exception as exc:
            session_discovery_error = f"{type(exc).__name__}: {exc}"
    duplicates = detect_duplicate_worktrees(records)
    loops = detect_looping_records(records)
    stale = detect_stale_records(records)
    cred = credential_present()
    usable_anonymous = bool(anonymous_pools_usable)
    verdict = "healthy"
    if duplicates or loops or stale or not models or session_discovery_error:
        verdict = "hold"
    if not cred and not usable_anonymous:
        verdict = "hold"

    projection = {
        "supervision_slice": "free_subagent_supervisor",
        "observed_at": _now_utc(),
        "read_only": True,
        "suggested_scale_verdict": verdict,
        "free_models_found": len(models),
        "credential_present": cred,
        "subagent_records": len(records),
        "session_discovery_error": session_discovery_error,
        "duplicate_worktrees": duplicates,
        "looping_records": loops,
        "stale_records": stale,
        "free_model_ids": sorted([m.get("id") for m in models if m.get("id")]),
        "free_model_types": model_types,
        "free_route_kinds": route_kind_counts,
        "provider_pools": provider_pools,
        "preferred_pool": preferred_pool,
        "reserve_only_pools": reserve_only_pools,
        "anonymous_pools_usable": anonymous_pools_usable,
        "kilo_anonymous_base_url": KILO_ANONYMOUS_BASE_URL,
        # Fail-closed defaults: the trace database was NOT read. Upstream commit
        # 7318b832 / cb91c47d added this whole block so a missing source can never
        # be mistaken for "observed, and empty" -- and so every trace_* key is
        # always present. Ported wholesale from origin/main during the rebase.
        "trace_records_count": 0,
        "trace_records_sample": [],
        "trace_provider_summary": {},
        "trace_route_kind_summary": {},
        "trace_model_attribution_summary": {},
        "trace_unresolved_router_aliases": [],
        "trace_records_status": "source_unavailable",
        "projections_note": (
            "Projection is read-only and advisory. It does not approve, admit, "
            "dispatch, or terminate subagent processes. Scale verdict is suggested, "
            "not enforced. Close stdin (< /dev/null) for noninteractive launches to "
            "avoid init waits. Free-cost gating requires both prompt and completion "
            "pricing to be zero; stale/duplicate/looping classifications are advisory. "
            "Provider pools are ordered to prefer truly zero-cost/anonymous pools "
            "(Kilo anonymous, LM Studio local) before reserved quota; Z.AI is "
            "reserve-only and excluded unless explicitly requested; provider state "
            "is surfaced as usable/rate_limited/quota_exhausted/payment_required/"
            "unqualified without spending paid models."
            + (" " + extra_note if extra_note else "")
        ),
    }

    if trace_exporter_path and trace_exporter_path.exists():
        try:
            import importlib.util

            SPEC = importlib.util.spec_from_file_location("trace_exporter", trace_exporter_path)
            trace_exporter_module = importlib.util.module_from_spec(SPEC)
            assert SPEC.loader is not None
            SPEC.loader.exec_module(trace_exporter_module)

            opencode_db_path = pathlib.Path.home() / ".local" / "share" / "opencode" / "opencode.db"
            if opencode_db_path.exists():
                raw_records = trace_exporter_module.export_sessions(opencode_db_path)
                projection["trace_records_status"] = "observed"
                trace_records = [_annotate_trace_session(r) for r in raw_records]
                projection["trace_records_count"] = len(trace_records)
                projection["trace_records_sample"] = [
                    {
                        k: v
                        for k, v in r.items()
                        if k
                        in (
                            "session_id",
                            "provider",
                            "requested_model",
                            "resolved_model",
                            "effective_model",
                            "route_kind",
                            "model_attribution",
                            "model_identity_complete",
                            "directory",
                            "cost",
                            "tokens",
                            "tools",
                            "git",
                            "time_created",
                            "time_updated",
                        )
                    }
                    for r in trace_records[:10]
                ]
                projection["trace_provider_summary"] = {}
                projection["trace_route_kind_summary"] = {}
                projection["trace_model_attribution_summary"] = {}
                projection["trace_unresolved_router_aliases"] = []
                for r in trace_records:
                    provider = r.get("provider", "unknown")
                    projection["trace_provider_summary"][provider] = (
                        projection["trace_provider_summary"].get(provider, 0) + 1
                    )
                    kind = r.get("route_kind", "exact")
                    projection["trace_route_kind_summary"][kind] = (
                        projection["trace_route_kind_summary"].get(kind, 0) + 1
                    )
                    attribution = r.get("model_attribution", "unknown")
                    projection["trace_model_attribution_summary"][attribution] = (
                        projection["trace_model_attribution_summary"].get(attribution, 0) + 1
                    )
                    if attribution == "unresolved-router-alias":
                        projection["trace_unresolved_router_aliases"].append(
                            {
                                "session_id": r.get("session_id"),
                                "provider": r.get("provider"),
                                "requested_model": r.get("requested_model"),
                                "directory": r.get("directory"),
                            }
                        )
        except Exception as e:
            projection["trace_exporter_error"] = str(e)

    return projection


def _now_utc() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")