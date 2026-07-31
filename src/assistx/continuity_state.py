from __future__ import annotations

from collections.abc import Mapping

from .continuity_falkor import FalkorContinuityStore
from .continuity_memory import InMemoryContinuityStore
from .continuity_types import (
    ContinuityConfig,
    ContinuityConflict,
    ContinuityError,
    ContinuityRejected,
    ContinuityStore,
    build_signed_event,
    canonical_json,
    event_signature,
    now_ms,
    verify_signed_event,
)

__all__ = [
    "ContinuityConfig",
    "ContinuityConflict",
    "ContinuityError",
    "ContinuityRejected",
    "ContinuityStore",
    "FalkorContinuityStore",
    "InMemoryContinuityStore",
    "build_signed_event",
    "canonical_json",
    "create_store_from_env",
    "event_signature",
    "now_ms",
    "verify_signed_event",
]


def create_store_from_env(env: Mapping[str, str]) -> ContinuityStore:
    config = ContinuityConfig(
        cluster_id=env.get(
            "ASSISTX_CONTINUITY_CLUSTER_ID",
            "assistx-fleet",
        ),
        node_id=env.get(
            "ASSISTX_CONTINUITY_NODE_ID",
            "continuity-node",
        ),
        signing_secret=env.get("ASSISTX_CONTINUITY_SIGNING_SECRET", ""),
        event_stream_maxlen=int(
            env.get("ASSISTX_CONTINUITY_EVENT_STREAM_MAXLEN", "20000")
        ),
        heartbeat_ttl_ms=int(
            env.get("ASSISTX_CONTINUITY_HEARTBEAT_TTL_MS", "45000")
        ),
        task_claim_ttl_ms=int(
            env.get("ASSISTX_CONTINUITY_TASK_CLAIM_TTL_MS", "120000")
        ),
        graph_name=env.get(
            "ASSISTX_CONTINUITY_GRAPH",
            "assistx_continuity",
        ),
        graph_projection_enabled=env.get(
            "ASSISTX_CONTINUITY_GRAPH_PROJECTION",
            "true",
        ).lower()
        in {"1", "true", "yes", "on"},
    )
    url = str(env.get("FALKORDB_URL") or "").strip()
    if url:
        return FalkorContinuityStore(config, url)
    if env.get(
        "ASSISTX_CONTINUITY_ALLOW_IN_MEMORY",
        "false",
    ).lower() in {"1", "true", "yes", "on"}:
        return InMemoryContinuityStore(config)
    raise RuntimeError(
        "FALKORDB_URL is required unless in-memory continuity mode is "
        "explicitly allowed"
    )
