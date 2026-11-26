"""Restricted evidence-first tools for the mobile agent. No model or shell access."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

PROFILE = "kipnerter-agent-v1"
FRESHNESS_SECONDS = 300
# Deliberately narrow first slice. Arbitrary prompts remain on the agent path.
_FLEET_STATUS = re.compile(
    r"(?:please )?(?:what(?:'s| is) (?:the )?(?:current )?(?:status of (?:the )?fleet|fleet status)|"
    r"(?:check|show(?: me)?|report) (?:the )?(?:current )?fleet status|fleet status|"
    r"how(?:'s| is) (?:the )?fleet(?: doing)?)\s*[?.!]*", re.I,
)


def is_fleet_status(messages: Iterable[Any]) -> bool:
    latest = next((m.content for m in reversed(list(messages)) if m.role == "user"), "")
    return bool(_FLEET_STATUS.fullmatch(" ".join(latest.split())))


def fleet_status(neo_factory: Callable[[], Any]) -> dict[str, Any]:
    """Return aggregate heartbeat observations, never endpoint or node secrets."""
    neo = neo_factory()
    try:
        with neo._session() as session:
            row = session.run(
                """
                MATCH (n:SwarmNode)
                WITH n, timestamp() AS now
                RETURN count(n) AS registered,
                    count(CASE WHEN n.last_seen_at_ts <= now
                        AND n.last_seen_at_ts >= now - $window THEN 1 END) AS recent,
                    count(CASE WHEN n.last_seen_at_ts IS NULL
                        OR n.last_seen_at_ts > now THEN 1 END) AS unknown,
                    max(CASE WHEN n.last_seen_at_ts <= now
                        THEN n.last_seen_at_ts END) AS last_seen_ms
                """, {"window": FRESHNESS_SECONDS * 1000},
            ).single()
            if row is None:
                raise ValueError("missing aggregate")
            registered, recent, unknown = (row[k] for k in ("registered", "recent", "unknown"))
            if any(type(x) is not int or x < 0 for x in (registered, recent, unknown)):
                raise ValueError("invalid aggregate")
            if recent + unknown > registered:
                raise ValueError("inconsistent aggregate")
            return {
                "profile": PROFILE,
                "source": "AssistX fleet registry (SwarmNode heartbeat records)",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "window_seconds": FRESHNESS_SECONDS,
                "registered": registered,
                "recent": recent,
                "stale": registered - recent - unknown,
                "unknown": unknown,
            }
    finally:
        neo.close()


def render_fleet_status(evidence: dict[str, Any]) -> str:
    return (
        f"Fleet registry status (checked {evidence['observed_at']}):\n"
        f"- Registered swarm nodes: {evidence['registered']}\n"
        f"- Heartbeat recorded within the last 5 minutes: {evidence['recent']}\n"
        f"- Older heartbeat: {evidence['stale']}\n"
        f"- Missing or invalid heartbeat time: {evidence['unknown']}\n"
        "These are registry observations, not live network probes. An older or missing "
        "heartbeat does not prove a node is offline. Model, service and device synchronization "
        "health have not been checked.\n"
        f"Source: {evidence['source']}."
    )
