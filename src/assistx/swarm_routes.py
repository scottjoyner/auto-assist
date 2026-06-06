from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict, Field

from .draft_model import DraftModelUnavailable, generate_draft
from .neo4j_client import Neo4jClient
from .outbox_client import OutboxClient
from .swarm_core import (
    EventConflictError,
    EventValidationError,
    action_requires_approval,
    delete_model_endpoint,
    fail_task,
    list_capabilities,
    list_model_endpoints,
    list_swarm_nodes,
    probe_model_endpoint,
    record_event,
    release_expired_task_leases,
    set_task_lease,
    upsert_model_endpoint,
    upsert_swarm_node,
)

router = APIRouter(tags=["swarm"])
_outbox_client: Optional[OutboxClient] = None


class EventEnvelopeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    metadata: Dict[str, Any] = Field(default_factory=dict)
    privacy: Dict[str, Any]


class SwarmNodeRegisterIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    model_config = ConfigDict(extra="ignore")

    status: str = "online"
    current_task_id: Optional[str] = None
    load: Dict[str, Any] = Field(default_factory=dict)
    services: List[Dict[str, Any]] = Field(default_factory=list)
    capabilities: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskFailIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent_id: str
    error_summary: str
    retryable: bool = True
    session_id: Optional[str] = None


class LeaseSweepIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    now_ms: Optional[int] = None


class ModelEndpointRegisterIn(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    model_endpoint_id: str
    node_id: str
    base_url: str
    provider: str = "lm_studio"
    status: str = "unknown"
    auth_type: str = "none"
    network_preference: Optional[str] = None
    purpose: Optional[str] = None


class DraftGenerateIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt: str = Field(min_length=1, max_length=8000)
    max_tokens: int = Field(default=256, ge=1, le=1024)


def _neo() -> Neo4jClient:
    return Neo4jClient()


def _outbox() -> OutboxClient:
    global _outbox_client
    if _outbox_client is None:
        _outbox_client = OutboxClient(auto_flush=True, flush_interval_s=30)
    return _outbox_client


# --- Auth ---
# Injected from api.py so swarm routes use the same Basic Auth as legacy endpoints.
_injected_auth_dependency = None
security = HTTPBasic(auto_error=False)


def set_auth_dependency(auth_func: Any) -> None:
    global _injected_auth_dependency
    _injected_auth_dependency = auth_func


def _default_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    if _injected_auth_dependency is not None:
        return _injected_auth_dependency(request, credentials)
    if credentials:
        return credentials.username
    return "system"


@router.post("/api/events")
def api_events(body: EventEnvelopeIn, user: str = Depends(_default_auth)):
    neo = _neo()
    try:
        return record_event(neo, body.model_dump())
    except EventValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except EventConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Event processing failed: {str(e)[:200]}")
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


@router.get("/api/swarm/model-endpoints")
def api_list_model_endpoints(user: str = Depends(_default_auth)):
    neo = _neo()
    try:
        return {"items": list_model_endpoints(neo)}
    finally:
        neo.close()


@router.post("/api/swarm/model-endpoints/register")
def api_register_model_endpoint(body: ModelEndpointRegisterIn, user: str = Depends(_default_auth)):
    neo = _neo()
    try:
        return {"endpoint": upsert_model_endpoint(neo, body.model_dump(exclude_none=True))}
    finally:
        neo.close()


@router.delete("/api/swarm/model-endpoints/{model_endpoint_id}")
def api_delete_model_endpoint(model_endpoint_id: str, user: str = Depends(_default_auth)):
    neo = _neo()
    try:
        result = delete_model_endpoint(neo, model_endpoint_id)
        if not result.get("deleted"):
            raise HTTPException(status_code=404, detail=result.get("error", "Model endpoint not found"))
        return result
    finally:
        neo.close()


@router.post("/api/swarm/model-endpoints/{model_endpoint_id}/probe")
def api_probe_model_endpoint(model_endpoint_id: str, user: str = Depends(_default_auth)):
    neo = _neo()
    try:
        endpoint = next(
            (item for item in list_model_endpoints(neo) if item.get("model_endpoint_id") == model_endpoint_id),
            None,
        )
        if endpoint is None:
            raise HTTPException(status_code=404, detail="Model endpoint not found")
        return probe_model_endpoint(neo, endpoint)
    finally:
        neo.close()


@router.post("/api/drafts/generate")
def api_generate_draft(body: DraftGenerateIn, user: str = Depends(_default_auth)):
    try:
        return generate_draft(body.prompt, body.max_tokens)
    except DraftModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


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


@router.get("/api/swarm/outbox/status")
def api_outbox_status(user: str = Depends(_default_auth)):
    return _outbox().get_stats()


@router.post("/api/swarm/outbox/flush")
def api_outbox_flush(max_attempts: Optional[int] = None, user: str = Depends(_default_auth)):
    delivered = _outbox().flush(max_attempts=max_attempts or 10)
    return {"flushed": delivered, "remaining": _outbox().get_stats()}


@router.get("/api/policy/voice-action")
def api_voice_policy(auth_state: str, action: str = "create_draft_task", risk_level: str = "low", user: str = Depends(_default_auth)):
    return {
        "auth_state": auth_state,
        "action": action,
        "risk_level": risk_level,
        "approval_required": action_requires_approval(auth_state, action, risk_level),
    }
