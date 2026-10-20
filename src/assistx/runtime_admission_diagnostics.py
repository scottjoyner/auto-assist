"""Read-only, non-authoritative overview of AssistX runtime admission evidence.

A count is never a signed approval, eligible routing handle, or runtime canary.
This module cannot renew evidence or mutate Neo4j.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

QUERIES = {
    "canonical": """
        MATCH (s:FleetProjectionState {name:'canonical'})
        RETURN s.generation AS generation, s.revision AS revision,
               s.status AS status, s.expires_at_ts AS expires_at_ts
        LIMIT 1
    """,
    "runtimes": """
        MATCH (r:RuntimeInstance)
        RETURN count(r) AS total,
               sum(CASE WHEN r.admitted = true THEN 1 ELSE 0 END) AS approved,
               sum(CASE WHEN r.admitted = true AND coalesce(r.expires_at_ts,0) > $now
                        AND toLower(coalesce(r.status,'unknown')) IN ['online','healthy','ready']
                        THEN 1 ELSE 0 END) AS eligible_now
    """,
    "models": """
        MATCH (m:LoadedModelInstance)
        RETURN count(m) AS total,
               sum(CASE WHEN m.admitted = true THEN 1 ELSE 0 END) AS approved,
               sum(CASE WHEN m.admitted = true AND coalesce(m.expires_at_ts,0) > $now
                        THEN 1 ELSE 0 END) AS eligible_now
    """,
    "access_paths": """
        MATCH (a:AccessPath)
        RETURN count(a) AS total,
               sum(CASE WHEN a.approved = true THEN 1 ELSE 0 END) AS approved,
               sum(CASE WHEN a.approved = true AND coalesce(a.expires_at_ts,0) > $now
                        THEN 1 ELSE 0 END) AS eligible_now
    """,
    "capacity": """
        MATCH (c:CapacityObservation)
        RETURN count(c) AS total,
               sum(CASE WHEN c.approved = true THEN 1 ELSE 0 END) AS approved,
               sum(CASE WHEN c.approved = true AND coalesce(c.expires_at_ts,0) > $now
                        THEN 1 ELSE 0 END) AS eligible_now
    """,
}


def summarize(read: Callable[[str, dict[str, int]], list[dict[str, Any]]],
              *, now_ms: int | None = None) -> dict[str, Any]:
    """Run fixed MATCH/RETURN queries only; return counts, never secrets/locations."""
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    state = (read(QUERIES["canonical"], {"now": now}) or [{}])[0]
    canonical_valid = (
        state.get("status") == "approved"
        and isinstance(state.get("generation"), int)
        and state["generation"] > 0
        and bool(state.get("revision"))
        and isinstance(state.get("expires_at_ts"), int)
        and state["expires_at_ts"] > now
    )
    categories = {}
    for name in ("runtimes", "models", "access_paths", "capacity"):
        item = (read(QUERIES[name], {"now": now}) or [{}])[0]
        categories[name] = {
            key: max(0, int(item.get(key) or 0))
            for key in ("total", "approved", "eligible_now")
        }
    reasons = []
    if not canonical_valid:
        reasons.append("canonical_approval_missing_invalid_or_expired")
    for name, data in categories.items():
        if not data["eligible_now"]:
            reasons.append(f"{name}_no_current_approved_evidence")
    return {
        "schema_version": 1,
        "observed_at_ms": now,
        "authority": "read_only_diagnostic_not_admission",
        "state": "blocked" if reasons else "candidate_evidence_present_not_verified",
        "canonical": {
            "generation": state.get("generation"),
            "status": state.get("status"),
            "revision": state.get("revision"),
            "expired_seconds": (
                max(0, (now - state["expires_at_ts"]) // 1000)
                if isinstance(state.get("expires_at_ts"), int) else None
            ),
            "valid": canonical_valid,
        },
        "categories": categories,
        "blockers": reasons,
        "side_effects": False,
    }
