from __future__ import annotations

import hmac
import json
import os
import re
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from .executor_security import ExecutorTokenCodec, ExecutorTokenError

_TASK_TOKEN_PATHS = (
    ("POST", re.compile(r"^/api/brain/context$")),
    ("POST", re.compile(r"^/api/tasks/([^/]+)/heartbeat$")),
    ("POST", re.compile(r"^/api/tasks/([^/]+)/complete$")),
)


def _bearer_from_scope(scope: dict[str, Any]) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == b"authorization":
            text = value.decode("latin-1").strip()
            if text.lower().startswith("bearer "):
                return text[7:].strip()
    return ""


def _bearer_header(value: str | None) -> str:
    text = str(value or "").strip()
    return text[7:].strip() if text.lower().startswith("bearer ") else ""


def _task_route(method: str, path: str) -> tuple[re.Match[str] | None, bool]:
    for expected, pattern in _TASK_TOKEN_PATHS:
        if method != expected:
            continue
        match = pattern.match(path)
        if match:
            return match, True
    return None, False


async def _read_body(receive: Any) -> tuple[bytes, Any]:
    body = bytearray()
    more = True
    while more:
        message = await receive()
        body.extend(message.get("body", b""))
        more = bool(message.get("more_body", False))
    sent = False

    async def replay() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    return bytes(body), replay


def read_claim_state(neo_factory: Callable[[], Any], task_id: str) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    neo = neo_factory()
    try:
        with neo._session() as session:
            row = session.run(
                """
                MATCH (t:Task {id:$task_id})
                OPTIONAL MATCH (s:FleetProjectionState {name:'canonical'})
                RETURN t.status AS status,
                       t.claimed_by AS agent_id,
                       t.claim_id AS claim_id,
                       t.lease_expires_at_ts AS lease_expires_at_ts,
                       t.execution_attempt AS execution_attempt,
                       s.generation AS projection_generation,
                       s.status AS projection_status,
                       s.expires_at_ts AS projection_expires_at_ts
                LIMIT 1
                """,
                {"task_id": task_id},
            ).single()
    finally:
        neo.close()
    if not row:
        return {
            "active": False,
            "task_id": task_id,
            "reason": "task_not_found",
            "checked_at_ms": now_ms,
        }
    result = dict(row)
    status = str(result.get("status") or "").upper()
    lease_expires = int(result.get("lease_expires_at_ts") or 0)
    projection_expires = int(result.get("projection_expires_at_ts") or 0)
    generation = int(result.get("projection_generation") or 0)
    active = (
        status in {"CLAIMED", "RUNNING", "PAUSING"}
        and bool(result.get("agent_id"))
        and bool(result.get("claim_id"))
        and lease_expires > now_ms
        and str(result.get("projection_status") or "").lower() == "approved"
        and generation > 0
        and projection_expires > now_ms
    )
    reason = "active" if active else "claim_or_projection_not_active"
    return {
        **result,
        "active": active,
        "reason": reason,
        "task_id": task_id,
        "checked_at_ms": now_ms,
    }


def assert_claim_matches(claims: dict[str, Any], state: dict[str, Any]) -> None:
    if not state.get("active"):
        raise ExecutorTokenError(str(state.get("reason") or "executor claim is not active"))
    expected = {
        "task_id": str(claims.get("task_id") or ""),
        "claim_id": str(claims.get("claim_id") or ""),
        "agent_id": str(claims.get("agent_id") or ""),
        "projection_generation": int(claims.get("projection_generation") or 0),
    }
    actual = {
        "task_id": str(state.get("task_id") or ""),
        "claim_id": str(state.get("claim_id") or ""),
        "agent_id": str(state.get("agent_id") or ""),
        "projection_generation": int(state.get("projection_generation") or 0),
    }
    if actual != expected:
        raise ExecutorTokenError("executor token no longer matches the active claim generation")
    token_expiry_ms = int(claims.get("exp") or 0) * 1000
    if token_expiry_ms > int(state.get("lease_expires_at_ts") or 0):
        raise ExecutorTokenError("executor token outlives the active task lease")


class LiveExecutorClaimMiddleware:
    """Revalidate task-token claims against Neo4j on every AssistX task call."""

    def __init__(self, app: Any, neo_factory: Callable[[], Any]) -> None:
        self.app = app
        self.neo_factory = neo_factory

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method") or "GET").upper()
        path = str(scope.get("path") or "")
        match, protected = _task_route(method, path)
        token = _bearer_from_scope(scope)
        if not protected or not token:
            await self.app(scope, receive, send)
            return
        body, replay = await _read_body(receive)
        try:
            claims = ExecutorTokenCodec.verifier_from_env().decode(
                token,
                audience="assistx-executor",
            )
            task_id = match.group(1) if match and match.lastindex else ""
            if not task_id and body:
                parsed = json.loads(body)
                task_id = str(parsed.get("task_id") or "") if isinstance(parsed, dict) else ""
            if task_id != str(claims.get("task_id") or ""):
                raise ExecutorTokenError("executor token task does not match request")
            assert_claim_matches(claims, read_claim_state(self.neo_factory, task_id))
        except (ExecutorTokenError, json.JSONDecodeError) as exc:
            await JSONResponse(status_code=401, content={"detail": str(exc)})(scope, replay, send)
            return
        await self.app(scope, replay, send)


def build_executor_claim_router(neo_factory: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/api/executor", tags=["executor-security"])

    @router.get("/claims/{task_id}/status")
    def claim_status(
        task_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        expected = os.getenv("ASSISTX_EXECUTOR_SERVICE_TOKEN", "").strip()
        supplied = _bearer_header(authorization)
        if not expected or not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="invalid executor service token")
        return read_claim_state(neo_factory, task_id)

    return router


def install_live_executor_claims(app: Any, neo_factory: Callable[[], Any]) -> None:
    if getattr(app.state, "live_executor_claims_installed", False):
        return
    app.add_middleware(LiveExecutorClaimMiddleware, neo_factory=neo_factory)
    app.include_router(build_executor_claim_router(neo_factory))
    app.state.live_executor_claims_installed = True
