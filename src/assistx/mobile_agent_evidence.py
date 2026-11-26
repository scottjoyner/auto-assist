"""Restricted evidence-first tools for the mobile agent. No model or shell access."""
from __future__ import annotations

import re
import time
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


# Server-owned capability inventory. Entries describe implemented read-only
# adapters, not permissions for arbitrary model-generated tool calls.
STANDARD_TOOLS = (
    ("fleet.status", "Aggregate heartbeat observations from fleet registry"),
    ("inference.status", "Fresh admitted runtime and model capability inventory"),
)
_INFERENCE_STATUS = re.compile(
    r"(?:please )?(?:inference status|model status|(?:what(?:'s| is) (?:the )?(?:current )?)?"
    r"(?:status of (?:the )?(?:inference|models|inference endpoints))|"
    r"(?:what|which) models are (?:available|admitted)|"
    r"(?:check|show(?: me)?|report) (?:the )?(?:current )?(?:inference|model) status)"
    r"\s*[?.!]*", re.I,
)


def is_inference_status(messages: Iterable[Any]) -> bool:
    latest = next((m.content for m in reversed(list(messages)) if m.role == "user"), "")
    return bool(_INFERENCE_STATUS.fullmatch(" ".join(latest.split())))


def inference_status(projection: dict[str, Any]) -> dict[str, Any]:
    now = int(time.time() * 1000)
    expires = projection.get("expires_at_ms")
    generated = projection.get("generated_at_ms")
    if (type(expires) is not int or type(generated) is not int
            or not 0 < generated <= now < expires):
        raise ValueError("projection missing or stale")
    providers = projection.get("providers")
    if not isinstance(providers, list):
        raise ValueError("invalid projection")
    runtimes = models = agent_runtimes = tool_models = 0
    families = set()
    for provider in providers:
        if not isinstance(provider, dict) or provider.get("enabled") is not True:
            continue
        rows = provider.get("models")
        if not isinstance(rows, list) or not rows:
            continue
        runtimes += 1
        agent = provider.get("allow_agent_runtime") is True
        for model in rows:
            if not isinstance(model, dict):
                raise ValueError("invalid model record")
            models += 1
            agent |= model.get("allow_agent_runtime") is True
            capabilities = model.get("capabilities") or []
            if not isinstance(capabilities, list):
                raise ValueError("invalid model capabilities")
            tool_models += int("tool_use" in capabilities or "tools" in capabilities)
            alias = str(model.get("alias") or "").lower()
            for family in ("ornith", "lfm3", "lfm2.5"):
                if family in alias:
                    families.add(family)
        agent_runtimes += int(agent)
    return {
        "source": "AssistX signed runtime projection", "observed_at": datetime.now(timezone.utc).isoformat(),
        "projection_expires_at": datetime.fromtimestamp(expires / 1000, timezone.utc).isoformat(),
        "runtimes": runtimes, "model_instances": models, "agent_runtimes": agent_runtimes,
        "tool_model_instances": tool_models, "recognized_families": sorted(families),
    }


def render_inference_status(evidence: dict[str, Any]) -> str:
    families = ", ".join(evidence["recognized_families"]) or "none of LFM/Ornith identified"
    return (
        f"Inference admission inventory (checked {evidence['observed_at']}):\n"
        f"- Admitted runtime entries: {evidence['runtimes']}\n"
        f"- Model instances: {evidence['model_instances']}\n"
        f"- Agent-capable runtime entries: {evidence['agent_runtimes']}\n"
        f"- Model instances advertising tools: {evidence['tool_model_instances']}\n"
        f"- Recognized model families: {families}\n"
        f"- Projection expires: {evidence['projection_expires_at']}\n"
        "Admission is not a live generation probe. Tool execution and end-to-end authentication "
        "are not established by this inventory.\n"
        f"Source: {evidence['source']}."
    )
