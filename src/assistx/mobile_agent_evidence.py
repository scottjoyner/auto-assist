"""Restricted evidence-first tools for the mobile agent. No model or shell access."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import httpx

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
    return bool(_FLEET_STATUS.fullmatch(" ".join(latest.replace("’", "'").split())))


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
    ("gateway.health", "Bounded health probe of the configured inference gateway"),
    ("workflow.status", "Aggregate queued and active fleet tasks"),
    ("knowledge.lookup", "Read curated agent operating handbook topics"),
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
    return bool(_INFERENCE_STATUS.fullmatch(" ".join(latest.replace("’", "'").split())))


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


_GATEWAY_STATUS = re.compile(
    r"(?:please )?(?:server (?:connection|status|health)|gateway (?:status|health)|"
    r"how(?:'s| is) (?:the )?server connection(?: and what data are we getting)?|"
    r"(?:check|show(?: me)?|report) (?:the )?(?:server connection|gateway health))"
    r"\s*[?.!]*", re.I,
)


def is_gateway_status(messages: Iterable[Any]) -> bool:
    latest = next((m.content for m in reversed(list(messages)) if m.role == "user"), "")
    return bool(_GATEWAY_STATUS.fullmatch(" ".join(latest.replace("’", "'").split())))


def gateway_health(base_url: str, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Probe only a server-owned URL, without sending conversation or credentials."""
    result = {
        "source": "AssistX backend to configured Auto-Router /health",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "state": "unknown", "http_status": None, "projection_active": None,
        "open_circuits": None, "claim_fence_rejections": None, "claim_status_recent_failure": None,
    }
    url = urlsplit(base_url)
    if (url.scheme not in {"http", "https"} or not url.hostname or
            url.username or url.password or url.query or url.fragment or
            url.path not in {"", "/"}):
        return result
    try:
        with httpx.Client(transport=transport, timeout=3, follow_redirects=False,
                          trust_env=False) as client:
            with client.stream("GET", base_url.rstrip("/") + "/health",
                               headers={"Accept": "application/json"}) as response:
                result["http_status"] = response.status_code
                result["state"] = "responded"
                if response.status_code != 200:
                    return result
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 65536:
                        return result
        import json
        payload = json.loads(content)
        if not isinstance(payload, dict):
            return result
        for key, wire_key in (("open_circuits", "open_circuits"),
                              ("claim_fence_rejections", "executor_claim_fence_rejections")):
            value = payload.get(wire_key)
            if type(value) is int and 0 <= value <= 2**63-1:
                result[key] = value
        recent = payload.get("executor_claim_status_recent_failure")
        if type(recent) is bool:
            result["claim_status_recent_failure"] = recent
        if payload.get("ok") is True and payload.get("status") == "ok":
            result["state"] = "healthy"
        elif payload.get("ok") is False:
            result["state"] = "unhealthy"
        if payload.get("context_projection_status") == "active" and payload.get("context_projection_degraded") is False:
            result["projection_active"] = True
        elif payload.get("context_projection_degraded") is True:
            result["projection_active"] = False
    except (httpx.HTTPError, ValueError):
        pass
    return result


def render_gateway_health(evidence: dict[str, Any]) -> str:
    status = evidence["http_status"]
    state = evidence["state"]
    detail = f"HTTP {status}; {state}" if status is not None else "not verified"
    projection = {True: "reported active", False: "reported degraded", None: "unknown"}[evidence["projection_active"]]
    diagnostics = []
    if evidence.get("open_circuits") is not None:
        diagnostics.append(f"- Open provider circuits: {evidence['open_circuits']}.\n")
    if evidence.get("claim_fence_rejections") is not None:
        diagnostics.append(f"- Claim-fence rejections reported by router: {evidence['claim_fence_rejections']} (process lifetime; not inference attempts).\n")
    recent = evidence.get("claim_status_recent_failure")
    if recent is True:
        diagnostics.append("- Router reports a recent executor claim-status service failure; generic inference is not established.\n")
    elif recent is False:
        diagnostics.append("- No recent claim-status service failure reported; this does not establish successful authorization.\n")
    return (
        f"Server connection (checked {evidence['observed_at']}):\n"
        "- This authenticated request reached the AssistX mobile API.\n"
        f"- Backend-to-inference-gateway health probe: {detail}.\n"
        f"- Gateway context projection: {projection}.\n"
        + "".join(diagnostics) +
        "- Model inference and executor authorization: not tested by this health probe.\n"
        "- Phone upload acknowledgements, last successful synchronization, and data received "
        "by the server: not verified. A local sensor reading or queued event is not evidence of delivery.\n"
        f"Source: {evidence['source']}."
    )


def capability_profile(*, evidence_configured: bool) -> dict[str, Any]:
    """Describe only server-owned capabilities wired in this deployment."""
    tools = [
        {"id": tool_id, "description": description, "read_only": True,
         "execution": "server_intent", "configured": evidence_configured}
        for tool_id, description in STANDARD_TOOLS
    ]
    return {
        "profile": PROFILE,
        "schema_version": 1,
        "tools": tools,
        "skills": [
            {"id": "fleet_diagnostics", "tools": ["fleet.status", "inference.status", "gateway.health", "workflow.status"]},
            {"id": "agent_handbook", "tools": ["knowledge.lookup"]},
        ],
        "knowledge_sources": [
            {"id": "fleet_registry", "access": "aggregate_heartbeat_only"},
            {"id": "runtime_projection", "access": "redacted_admission_inventory"},
            {"id": "gateway_health", "access": "bounded_health_probe"},
            {"id": "workflow_registry", "access": "aggregate_task_counts_only"},
            {"id": "agent_handbook_v1", "access": "curated_static_topics_only"},
        ] if evidence_configured else [],
        "native_model_tool_calls": False,
        "generic_knowledge_search": False,
        "device_sync_receipts": False,
        "note": "Configured capabilities are not a live readiness or authorization witness.",
        "example_questions": ["Fleet overview", "Fleet status and model status", "What tools do you have?"],
    }


_WORKFLOW_INTENT = re.compile(
    r"(?:please )?(?:workflow status|fleet work status|task queue status|"
    r"what (?:is|are) (?:the )?(?:fleet|agents) (?:working on|doing)|"
    r"(?:check|show(?: me)?|report) (?:the )?(?:workflow status|task queue))"
    r"\s*[?.!]*", re.I,
)
_HANDBOOK_INTENT = re.compile(
    r"(?:please )?(?:agent handbook|how does agent auto work|"
    r"what tools (?:do you have|can you use)|what can agent auto do|"
    r"how (?:do i|can i) verify (?:location )?(?:sync|uploads))\s*[?.!]*", re.I,
)


def _last_user_text(messages: Iterable[Any]) -> str:
    return " ".join(next((m.content for m in reversed(list(messages))
                          if m.role == "user"), "").replace("’", "'").split())


def is_workflow_status(messages: Iterable[Any]) -> bool:
    return bool(_WORKFLOW_INTENT.fullmatch(_last_user_text(messages)))


def is_handbook_query(messages: Iterable[Any]) -> bool:
    return bool(_HANDBOOK_INTENT.fullmatch(_last_user_text(messages)))


def workflow_status(neo_factory: Callable[[], Any]) -> dict[str, Any]:
    """No task titles, messages, ownership, outputs or identifiers leave the graph."""
    neo = neo_factory()
    try:
        with neo._session() as session:
            row = session.run(
                """MATCH (t:Task) WHERE t.status IN $statuses
                   RETURN count(CASE WHEN t.status = 'READY' THEN 1 END) AS ready,
                          count(CASE WHEN t.status = 'CLAIMED' THEN 1 END) AS claimed,
                          count(CASE WHEN t.status = 'RUNNING' THEN 1 END) AS running""",
                {"statuses": ["READY", "CLAIMED", "RUNNING"]},
            ).single()
            if row is None:
                raise ValueError("missing workflow aggregate")
            counts = {k: row[k] for k in ("ready", "claimed", "running")}
            if any(type(v) is not int or v < 0 for v in counts.values()):
                raise ValueError("invalid workflow aggregate")
            return {**counts, "observed_at": datetime.now(timezone.utc).isoformat()}
    finally:
        neo.close()


def render_workflow_status(evidence: dict[str, Any]) -> str:
    return (
        f"Fleet work registry (checked {evidence['observed_at']}):\n"
        f"- Ready: {evidence['ready']}\n"
        f"- Claimed: {evidence['claimed']}\n"
        f"- Running: {evidence['running']}\n"
        "These are recorded task states, not proof of active computation or successful completion. "
        "Private task contents and individual identities are not exposed.\n"
        "Source: AssistX Task registry."
    )


def render_handbook(messages: Iterable[Any]) -> str:
    """Versioned, server-authored knowledge; never interpolate user or graph text."""
    query = _last_user_text(messages).lower()
    if "verify" in query:
        body = (
            "Local capture, queued events, historical acknowledgements and server persistence "
            "are different observations. A successful gateway probe does not verify an upload. "
            "Check the local sync queue and the latest retained acknowledgement; compare them "
            "with a server-side receipt for the same event or device when available. "
            "This agent does not yet have a server-side receipt lookup."
        )
    else:
        tools = "; ".join(f"{tool}: {description}" for tool, description in STANDARD_TOOLS)
        body = (
            "Agent Auto uses the authenticated AssistX gateway. Its deterministic evidence tools "
            "can answer recognized read-only questions without invoking a model. "
            f"Configured tools in this release: {tools}. "
            "General conversation is a separate executor path; a healthy gateway or an admitted "
            "model does not prove that path works. Native model tool calls, unrestricted graph "
            "search, device receipt lookup and administrative actions are not provided by this profile."
        )
    return body + "\nSource: kipnerter-agent-v1 curated operating handbook, revision 1."


_OVERVIEW_INTENT = re.compile(
    r"(?:please )?(?:fleet overview|fleet health summary|fleet operational overview|"
    r"how(?:'s| is) (?:the )?fleet doing overall|"
    r"(?:give|show) me (?:a |the )?fleet (?:overview|health summary))\s*[?.!]*", re.I,
)


def overview_sections(messages: Iterable[Any]) -> tuple[str, ...]:
    """Recognize explicit overview and bounded combinations, not arbitrary commands."""
    from types import SimpleNamespace
    query = _last_user_text(messages)
    if _OVERVIEW_INTENT.fullmatch(query):
        return ("fleet", "inference", "workflow", "gateway")
    matchers = (("fleet", is_fleet_status), ("inference", is_inference_status),
                ("workflow", is_workflow_status), ("gateway", is_gateway_status))
    # Existing single-intent phrases (including the gateway's compound phrase)
    # retain precedence. Only two independently recognized clauses are accepted.
    if any(m(messages) for _, m in matchers):
        return ()
    parts = re.split(r"\s+and\s+", query, flags=re.I)
    if len(parts) != 2:
        return ()
    sections = []
    for part in parts:
        matching = [name for name, matcher in matchers
                    if matcher([SimpleNamespace(role="user", content=part)])]
        if len(matching) != 1 or matching[0] in sections:
            return ()
        sections.append(matching[0])
    return tuple(sections)


def render_overview(sections: tuple[str, ...], *, neo_factory: Callable[[], Any],
                    projection_factory: Callable[[], dict[str, Any]], gateway_url: str) -> str:
    """Only fixed read-only adapters. Preserve partial success and source freshness."""
    handlers = {
        "fleet": lambda: render_fleet_status(fleet_status(neo_factory)),
        "inference": lambda: render_inference_status(inference_status(projection_factory())),
        "workflow": lambda: render_workflow_status(workflow_status(neo_factory)),
        "gateway": lambda: render_gateway_health(gateway_health(gateway_url)),
    }
    if not 2 <= len(sections) <= 4 or len(set(sections)) != len(sections) or any(s not in handlers for s in sections):
        raise ValueError("invalid overview sections")
    output = ["Fleet evidence overview. Each section is independently observed; this is not a single atomic snapshot."]
    for section in sections:
        try:
            output.append(handlers[section]())
        except Exception:
            output.append(f"{section.capitalize()} evidence unavailable; no current status inferred.")
    return "\n\n".join(output)
