"""Small non-admitting machine identity route for the exact deployed API base.

Machine identity never grants legacy Task authority, even alongside valid Basic.
No Neo4j, provider, session, signer, projection or Task reads occur here.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .node_identity import verify_node_token

_NODE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_LEGACY_TASK = re.compile(r"^/api/tasks/[^/]+/(?:claim|heartbeat|complete|checkpoint)$")


def _strict_registry() -> dict[str, str]:
    """Reject malformed and duplicate-key registries rather than reinterpreting."""
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate node identity")
            out[key] = value
        return out

    try:
        payload = json.loads(os.getenv("ASSISTX_FLEET_NODE_TOKENS", ""),
                             object_pairs_hook=unique_pairs)
        if not isinstance(payload, dict):
            return {}
        if not all(isinstance(k, str) and isinstance(v, str) and v
                   for k, v in payload.items()):
            return {}
        return payload
    except (ValueError, TypeError):
        return {}


def build_identity_only_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/fleet-node/identity")
    def read_identity(
        x_fleet_node_id: str | None = Header(default=None),
        x_fleet_node_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if (not x_fleet_node_id or not _NODE_ID.fullmatch(x_fleet_node_id)
                or not x_fleet_node_token or len(x_fleet_node_token) > 512):
            raise HTTPException(status_code=403, detail="machine identity required")
        registry = _strict_registry()
        if verify_node_token(registry, x_fleet_node_id, x_fleet_node_token):
            raise HTTPException(status_code=403, detail="invalid machine identity")
        return {"machine_identity_verified": True, "node_id": x_fleet_node_id,
                "task_admission": "HOLD", "legacy_task_authorized": False}

    return router


def install_machine_legacy_task_guard(app: Any) -> None:
    """Deny node credential headers on five operator-era Task routes.

    No credential value is logged, persisted, granted Task authority or sent to
    graph. The legacy operator Basic route without machine headers is unchanged.
    """
    @app.middleware("http")
    async def deny_machine_legacy_task(request: Request, call_next):
        path = request.url.path
        has_node_header = ("x-fleet-node-id" in request.headers
                           or "x-fleet-node-token" in request.headers)
        is_task = ((request.method == "GET" and path == "/api/agent/tasks")
                   or (request.method == "POST" and _LEGACY_TASK.fullmatch(path)))
        if has_node_header and is_task:
            return JSONResponse(status_code=403,
                                content={"detail": "machine Task authority unavailable"})
        return await call_next(request)
