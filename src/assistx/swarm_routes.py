from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .neo4j_client import Neo4jClient
from .swarm_core import (
    EventConflictError,
    EventValidationError,
    action_requires_approval,
    ensure_swarm_schema,
    fail_task,
    list_capabilities,
    list_swarm_nodes,
    record_event,
    release_expired_task_leases,
    set_task_lease,
    upsert_swarm_node,
)

router = APIRouter(tags=["swarm"])


class EventEnvelopeIn(BaseModel):
    event_id: str
    event_type: str
    source_repo: str
    source_service: str
    node_id: str
    occurred_at: str
    idempotency_key: str
    schema_version: str
    subject: Dict[str, Any]
    payload: Dict[str, Any] = Field(default_factory=dict)
    artifact_refs: List[Dict[str, Any]] = Field(default_factory=list)
    privacy: Dict[str, Any]


class SwarmNodeRegisterIn(BaseModel):
    node_id: str
    hostname: Optional[str] = None
    display_name: Optional[str] = None
    status: str = "online"
    roles: List[str] = Field(default_factory=list)
    tailscale_ip: Optional[str] = None
    tailscale_name: Optional[str] = None
    lan_ip: Optional[str] = None
    os: Optional[str] = None
    arch: Optional[str] = None
    cpu_model: Optional[str] = None
    cpu_threads: Optional[int] = None
    memory_gb: Optional[float] = None
    gpu: Optional[str] = None
    gpu_memory_gb: Optional[float] = None
    power_profile: Optional[str] = None
    storage_profile: Optional[str] = None
    capabilities: List[Dict[str, Any]] = Field(default_factory=list)
    services: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SwarmHeartbeatIn(BaseModel):
    status: str = "online"
    current_task_id: Optional[str] = None
    load: Dict[str, Any] = Field(default_factory=dict)
    services: List[Dict[str, Any]] = Field(default_factory=list)
    capabilities: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskFailIn(BaseModel):
    agent_id: str
    error_summary: str
    retryable: bool = True
    session_id: Optional[str] = None


class LeaseSweepIn(BaseModel):
    now_ms: Optional[int] = None


def _neo() -> Neo4jClient:
    return Neo4jClient()


# Auth function to be injected from api.py
_injected_auth_dependency = None


def set_auth_dependency(auth_func: Any) -> None:
    """Called from api.py to inject the real auth dependency."""
    global _injected_auth_dependency
    _injected_auth_dependency = auth_func


def _swarm_auth_wrapper(request: Request, credentials: Optional[Any] = None) -> str:
    """Wrapper that delegates to the injected auth function."""
    if _injected_auth_dependency is None:
        # Fallback: return a system user for trusted-network operation
        return "system"
    # Call the injected auth function
    return _injected_auth_dependency(request, credentials)


@router.post("/api/events")
def api_events(body: EventEnvelopeIn, user: str = Depends(_default_auth)):
    neo = _neo()
    try:
        return record_event(neo, body.model_dump())
    except EventValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except EventConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    finally:
        neo.close()


@router.post("/api/swarm/nodes/register")
def api_register_node(body: SwarmNodeRegisterIn, user: str = Depends(_default_auth)):
    neo = _neo()
    try:
        return {"node": upsert_swarm_node(neo, body.model_dump(exclude_none=True))}
    finally:
        neo.close()


@router.post("/api/swarm/nodes/{node_id}/heartbeat")
def api_node_heartbeat(node_id: str, body: SwarmHeartbeatIn, user: str = Depends(_default_auth)):
    neo = _neo()
    try:
        payload = body.model_dump(exclude_none=True)
        payload["node_id"] = node_id
        return {"node": upsert_swarm_node(neo, payload)}
    finally:
        neo.close()


@router.get("/api/swarm/nodes")
def api_list_nodes(limit: int = 100, user: str = Depends(_default_auth)):
    neo = _neo()
    try:
        return {"items": list_swarm_nodes(neo, limit=limit)}
    finally:
        neo.close()


@router.get("/api/swarm/capabilities")
def api_list_caps(limit: int = 200, user: str = Depends(_default_auth)):
    neo = _neo()
    try:
        return {"items": list_capabilities(neo, limit=limit)}
    finally:
        neo.close()


@router.post("/api/tasks/{task_id}/fail")
def api_fail_task(task_id: str, body: TaskFailIn, user: str = Depends(_default_auth)):
    neo = _neo()
    try:
        task = fail_task(neo, task_id, body.agent_id, body.error_summary, body.retryable, body.session_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return {"task": task}
    finally:
        neo.close()


@router.post("/api/tasks/leases/release-expired")
def api_release_expired_leases(body: LeaseSweepIn, user: str = Depends(_default_auth)):
    neo = _neo()
    try:
        return {"released": release_expired_task_leases(neo, now_ms=body.now_ms)}
    finally:
        neo.close()


@router.get("/api/policy/voice-action")
def api_voice_policy(auth_state: str, action: str = "create_draft_task", risk_level: str = "low", user: str = Depends(_default_auth)):
    return {
        "auth_state": auth_state,
        "action": action,
        "risk_level": risk_level,
        "approval_required": action_requires_approval(auth_state, action, risk_level),
    }


# Auth function to be injected from api.py - provides access to the same auth as legacy endpoints
_injected_auth_func = None


def set_auth_dependency(auth_func: Any) -> None:
    """Called from api.py to inject the real auth dependency."""
    global _injected_auth_func
    _injected_auth_func = auth_func


def _get_auth_dependency() -> Any:
    """Returns the injected auth function or raises an error."""
    if _injected_auth_func is None:
        raise RuntimeError("Swarm auth dependency not injected. Call set_auth_dependency from api.py.")
    return _injected_auth_func
