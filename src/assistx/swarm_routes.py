from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, FastAPI, HTTPException
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


@router.post("/api/events")
def api_events(body: EventEnvelopeIn):
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
def api_register_node(body: SwarmNodeRegisterIn):
    neo = _neo()
    try:
        ensure_swarm_schema(neo)
        return {"node": upsert_swarm_node(neo, body.model_dump(exclude_none=True))}
    finally:
        neo.close()


@router.post("/api/swarm/nodes/{node_id}/heartbeat")
def api_node_heartbeat(node_id: str, body: SwarmHeartbeatIn):
    neo = _neo()
    try:
        payload = body.model_dump(exclude_none=True)
        payload["node_id"] = node_id
        return {"node": upsert_swarm_node(neo, payload)}
    finally:
        neo.close()


@router.get("/api/swarm/nodes")
def api_list_nodes(limit: int = 100):
    neo = _neo()
    try:
        return {"items": list_swarm_nodes(neo, limit=limit)}
    finally:
        neo.close()


@router.get("/api/swarm/capabilities")
def api_list_caps(limit: int = 200):
    neo = _neo()
    try:
        return {"items": list_capabilities(neo, limit=limit)}
    finally:
        neo.close()


@router.post("/api/tasks/{task_id}/fail")
def api_fail_task(task_id: str, body: TaskFailIn):
    neo = _neo()
    try:
        task = fail_task(neo, task_id, body.agent_id, body.error_summary, body.retryable, body.session_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return {"task": task}
    finally:
        neo.close()


@router.post("/api/tasks/leases/release-expired")
def api_release_expired_leases(body: LeaseSweepIn):
    neo = _neo()
    try:
        return {"released": release_expired_task_leases(neo, now_ms=body.now_ms)}
    finally:
        neo.close()


@router.get("/api/policy/voice-action")
def api_voice_policy(auth_state: str, action: str = "create_draft_task", risk_level: str = "low"):
    return {
        "auth_state": auth_state,
        "action": action,
        "risk_level": risk_level,
        "approval_required": action_requires_approval(auth_state, action, risk_level),
    }


_INSTALLED = False
_ORIGINAL_FASTAPI_INIT = None
_ORIGINAL_ENSURE_SCHEMA = None
_ORIGINAL_CLAIM_TASK = None
_ORIGINAL_HEARTBEAT_TASK = None


def install_swarm_routes_patch() -> None:
    global _INSTALLED, _ORIGINAL_FASTAPI_INIT, _ORIGINAL_ENSURE_SCHEMA, _ORIGINAL_CLAIM_TASK, _ORIGINAL_HEARTBEAT_TASK
    if _INSTALLED:
        return
    _INSTALLED = True

    _ORIGINAL_FASTAPI_INIT = FastAPI.__init__

    def _swarm_fastapi_init(self, *args, **kwargs):
        _ORIGINAL_FASTAPI_INIT(self, *args, **kwargs)
        self.include_router(router)

    FastAPI.__init__ = _swarm_fastapi_init

    _ORIGINAL_ENSURE_SCHEMA = Neo4jClient.ensure_schema

    def _ensure_schema_with_swarm(self: Neo4jClient):
        _ORIGINAL_ENSURE_SCHEMA(self)
        ensure_swarm_schema(self)

    Neo4jClient.ensure_schema = _ensure_schema_with_swarm

    _ORIGINAL_CLAIM_TASK = Neo4jClient.claim_task

    def _claim_task_with_lease(self: Neo4jClient, *args, **kwargs):
        result = _ORIGINAL_CLAIM_TASK(self, *args, **kwargs)
        if isinstance(result, dict) and result.get("claimed") and result.get("task"):
            task_id = result["task"].get("id")
            if task_id:
                set_task_lease(self, task_id, lease_seconds=int(kwargs.get("lease_seconds") or 900))
                refreshed = self.get_task(task_id)
                if refreshed:
                    result["task"] = refreshed
        return result

    Neo4jClient.claim_task = _claim_task_with_lease

    _ORIGINAL_HEARTBEAT_TASK = Neo4jClient.heartbeat_task

    def _heartbeat_task_extends_lease(self: Neo4jClient, task_id: str, *args, **kwargs):
        result = _ORIGINAL_HEARTBEAT_TASK(self, task_id, *args, **kwargs)
        if result:
            set_task_lease(self, task_id, lease_seconds=900)
            refreshed = self.get_task(task_id)
            return refreshed or result
        return result

    Neo4jClient.heartbeat_task = _heartbeat_task_extends_lease
