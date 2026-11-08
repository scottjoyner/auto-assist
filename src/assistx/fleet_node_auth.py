"""Fail-closed authentication for fleet-node-agent workload endpoints.

The fleet node agent (``assistx.fleet_node_agent``) and the recovery island
agent authenticate with a per-node identity: ``X-Fleet-Node-Id`` names the
node and ``X-Fleet-Node-Token`` carries its control-plane token. The legacy
general Basic credential remains supported on the same endpoints for
operators, but a request that presents *partial or invalid* node credentials
is always rejected — it never falls back to weaker authentication.

The per-node token is only ever verified against the ``ASSISTX_FLEET_NODE_TOKENS``
JSON object (``{"<node-id>": "<token>"}``) with a constant-time comparison. Both
values are read from the environment at request time so configuration can be
rotated without code changes.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .node_identity import verify_node_token


class FleetNodeAuthHold(Exception):
    """A fleet-node credential header was presented but could not be verified."""


def _json_object_env(name: str) -> dict[str, Any]:
    try:
        value = json.loads(os.getenv(name, "{}"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def evaluate_fleet_node_identity(
    node_id: str | None,
    token: str | None,
) -> str | None:
    """Resolve the per-node identity for a request.

    Returns ``None`` when no node credential headers were presented (caller
    may then fall back to the legacy Basic credential). Returns the verified
    node id when the credentials are registered and match. Raises
    :class:`FleetNodeAuthHold` when node credentials were presented but are
    missing/unknown/invalid — callers must reject the request and must not
    fall back to weaker authentication.
    """
    if node_id is None and token is None:
        return None
    node_id = (node_id or "").strip()
    tokens = _json_object_env("ASSISTX_FLEET_NODE_TOKENS")
    error = verify_node_token(tokens, node_id or None, token or None)
    if error:
        raise FleetNodeAuthHold(error)
    return node_id


def fleet_node_binding_error(
    verified_node_id: str | None,
    claimed_agent_id: str | None,
) -> str | None:
    """Return an error message when a node-authenticated request targets another node."""
    if verified_node_id is None:
        # Legacy Basic-authenticated requests are not bound to a node identity.
        return None
    if str(claimed_agent_id or "") != verified_node_id:
        return "fleet node identity mismatch"
    return None
