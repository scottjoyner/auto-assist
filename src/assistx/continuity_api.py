from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading
from collections.abc import Mapping
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from .continuity_budget import plan_from_env
from .continuity_coordinator import ContinuityCoordinator
from .continuity_state import (
    ContinuityConflict,
    ContinuityRejected,
    ContinuityStore,
    FalkorContinuityStore,
    build_signed_event,
    create_store_from_env,
)

app = FastAPI(
    title="AssistX Continuity Plane",
    version="1.0.0",
    description=(
        "Bounded hot-state, lease, task, and runtime-projection service for "
        "recovery operation without a live Neo4j dependency."
    ),
)

_STORE: ContinuityStore | None = None
_STORE_LOCK = threading.Lock()


class HeartbeatRequest(BaseModel):
    node_id: str
    hostname: str | None = None
    status: str = "healthy"
    capabilities: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    active_slots: int = 0
    max_slots: int = 0
    memory_total_mb: int = 0
    memory_available_mb: int = 0
    runtime_models: list[dict[str, Any] | str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    ttl_ms: int | None = None
    observed_at_ms: int | None = None


class EpochAdvanceRequest(BaseModel):
    epoch: int = Field(ge=1)
    fence_proof: str


class RoleLeaseRequest(BaseModel):
    holder_node_id: str
    epoch: int = Field(ge=0)
    ttl_ms: int = Field(default=30_000, ge=5_000, le=300_000)
    fence_proof: str


class TaskRequest(BaseModel):
    task_id: str | None = None
    idempotency_key: str | None = None
    title: str
    kind: str = "continuity"
    epoch: int | None = None
    priority: int = Field(default=50, ge=0, le=100)
    required_capabilities: list[str] = Field(default_factory=list)
    preferred_nodes: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    ttl_ms: int = Field(
        default=86_400_000,
        ge=60_000,
        le=604_800_000,
    )


class TaskClaimRequest(BaseModel):
    node_id: str
    capabilities: list[str] = Field(default_factory=list)
    epoch: int = Field(ge=0)
    ttl_ms: int | None = Field(default=None, ge=10_000, le=900_000)


class TaskCompleteRequest(BaseModel):
    node_id: str
    claim_token: str
    status: str
    result: dict[str, Any] = Field(default_factory=dict)


class DocumentRequest(BaseModel):
    payload: dict[str, Any]
    epoch: int = Field(ge=0)
    ttl_ms: int = Field(default=120_000, ge=10_000, le=86_400_000)


class EventRequest(BaseModel):
    event: dict[str, Any]


class RouterEventRequest(BaseModel):
    event_type: str | None = None
    type: str | None = None
    event_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    task_id: str | None = None
    node_id: str | None = None
    runtime_node_id: str | None = None
    status: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "allow"}


def get_store() -> ContinuityStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = create_store_from_env(os.environ)
    return _STORE


def set_store_for_testing(store: ContinuityStore | None) -> None:
    global _STORE
    with _STORE_LOCK:
        _STORE = store


def _api_token() -> str:
    return str(os.getenv("ASSISTX_CONTINUITY_API_TOKEN") or "")


def require_token(
    x_continuity_token: str | None = Header(
        default=None,
        alias="X-Continuity-Token",
    ),
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
) -> str:
    expected = _api_token()
    if (
        len(expected) >= 16
        and x_continuity_token
        and hmac.compare_digest(x_continuity_token, expected)
    ):
        return "continuity-token-client"

    basic_user = str(os.getenv("ASSISTX_CONTINUITY_BASIC_AUTH_USER") or "")
    basic_pass = str(os.getenv("ASSISTX_CONTINUITY_BASIC_AUTH_PASS") or "")
    if (
        authorization
        and authorization.startswith("Basic ")
        and basic_user
        and basic_pass
    ):
        try:
            decoded = base64.b64decode(
                authorization[6:],
                validate=True,
            ).decode("utf-8")
            supplied_user, supplied_pass = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            supplied_user, supplied_pass = "", ""
        if hmac.compare_digest(
            supplied_user,
            basic_user,
        ) and hmac.compare_digest(supplied_pass, basic_pass):
            return supplied_user

    if len(expected) < 16 and not (basic_user and basic_pass):
        raise HTTPException(
            status_code=503,
            detail="continuity API authentication is not configured",
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid continuity credentials",
    )


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ContinuityConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ContinuityRejected):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def _record_internal_event(
    store: ContinuityStore,
    *,
    kind: str,
    payload: Mapping[str, Any],
    durability: str,
    idempotency_key: str,
) -> dict[str, Any]:
    event = build_signed_event(
        cluster_id=store.config.cluster_id,
        source_node_id=store.config.node_id,
        epoch=store.current_epoch(),
        kind=kind,
        payload=payload,
        durability=durability,
        secret=store.config.signing_secret,
        idempotency_key=idempotency_key,
    )
    return store.append_event(event)


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        store = get_store()
        backend_ok = True
        memory = None
        if isinstance(store, FalkorContinuityStore):
            backend_ok = store.ping()
            memory = store.memory_info()
        return {
            "ok": backend_ok,
            "service": "assistx-continuity",
            "backend": type(store).__name__,
            "cluster_id": store.config.cluster_id,
            "node_id": store.config.node_id,
            "epoch": store.current_epoch(),
            "memory": memory,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/ready")
def ready() -> dict[str, Any]:
    return health()


@app.get(
    "/v1/continuity/status",
    dependencies=[Depends(require_token)],
)
def continuity_status() -> dict[str, Any]:
    store = get_store()
    snapshot = store.snapshot()
    snapshot["memory_plan"] = plan_from_env()
    snapshot["coordinator"] = ContinuityCoordinator(
        store,
        node_id=store.config.node_id,
    ).status()
    return snapshot


@app.post(
    "/v1/continuity/epoch/advance",
    dependencies=[Depends(require_token)],
)
def advance_epoch(request: EpochAdvanceRequest) -> dict[str, Any]:
    store = get_store()
    try:
        state = store.advance_epoch(request.epoch, request.fence_proof)
        event = _record_internal_event(
            store,
            kind="recovery.epoch.advanced",
            payload=state,
            durability="durable",
            idempotency_key=f"recovery.epoch:{request.epoch}",
        )
        return {"state": state, "event": event}
    except Exception as exc:
        raise _translate_error(exc) from exc


@app.post(
    "/v1/continuity/heartbeat",
    dependencies=[Depends(require_token)],
)
def heartbeat(request: HeartbeatRequest) -> dict[str, Any]:
    store = get_store()
    try:
        record = store.record_heartbeat(
            request.model_dump(exclude_none=True)
        )
        _record_internal_event(
            store,
            kind="service.observed",
            payload=record,
            durability="recoverable",
            idempotency_key=(
                f"service.observed:{record['node_id']}:"
                f"{record['observed_at_ms']}"
            ),
        )
        return record
    except Exception as exc:
        raise _translate_error(exc) from exc


@app.post(
    "/v1/continuity/events",
    dependencies=[Depends(require_token)],
)
def append_event(request: EventRequest) -> dict[str, Any]:
    try:
        return get_store().append_event(request.event)
    except Exception as exc:
        raise _translate_error(exc) from exc


@app.post("/api/events", dependencies=[Depends(require_token)])
def ingest_router_event(request: RouterEventRequest) -> dict[str, Any]:
    store = get_store()
    raw = request.model_dump(exclude_none=True)
    extra = getattr(request, "__pydantic_extra__", None) or {}
    raw.update(extra)
    kind = str(request.event_type or request.type or "router.event")
    durability = (
        "durable"
        if kind in {"request.completed", "request.failed", "task.completed"}
        else "recoverable"
    )
    identity = request.event_id or request.request_id or request.correlation_id
    if not identity:
        identity = hashlib.sha256(
            (
                kind
                + str(raw.get("task_id") or "")
                + str(raw.get("status") or "")
            ).encode()
        ).hexdigest()
    try:
        return _record_internal_event(
            store,
            kind=f"router.{kind}",
            payload=raw,
            durability=durability,
            idempotency_key=f"router:{identity}:{kind}",
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@app.put(
    "/v1/continuity/documents/{name}",
    dependencies=[Depends(require_token)],
)
def put_document(name: str, request: DocumentRequest) -> dict[str, Any]:
    try:
        return get_store().put_document(
            name=name,
            payload=request.payload,
            epoch=request.epoch,
            ttl_ms=request.ttl_ms,
        )
    except Exception as exc:
        raise _translate_error(exc) from exc


@app.get(
    "/v1/continuity/documents/{name}",
    dependencies=[Depends(require_token)],
)
def get_document(name: str) -> dict[str, Any]:
    record = get_store().get_document(name)
    if not record:
        raise HTTPException(
            status_code=404,
            detail="continuity document not found or expired",
        )
    return record


@app.get(
    "/api/router/runtime-projection",
    dependencies=[Depends(require_token)],
)
def runtime_projection() -> dict[str, Any]:
    record = get_store().get_document("runtime-projection")
    if not record:
        raise HTTPException(
            status_code=503,
            detail="no fresh continuity runtime projection",
        )
    return dict(record["payload"])


@app.get(
    "/api/router/context-projection",
    dependencies=[Depends(require_token)],
)
def context_projection() -> dict[str, Any]:
    record = get_store().get_document("context-projection")
    if not record:
        return {
            "schema_version": 1,
            "epoch": get_store().current_epoch(),
            "generated_at_ms": 0,
            "contexts": [],
        }
    return dict(record["payload"])


@app.post(
    "/v1/continuity/roles/{role}/acquire",
    dependencies=[Depends(require_token)],
)
def acquire_role(role: str, request: RoleLeaseRequest) -> dict[str, Any]:
    store = get_store()
    try:
        lease = store.acquire_role_lease(
            role=role,
            holder_node_id=request.holder_node_id,
            epoch=request.epoch,
            ttl_ms=request.ttl_ms,
            fence_proof=request.fence_proof,
        )
        _record_internal_event(
            store,
            kind="role.lease.acquired",
            payload=lease,
            durability="recoverable",
            idempotency_key=(
                f"role:{role}:{request.holder_node_id}:{lease['nonce']}"
            ),
        )
        return lease
    except Exception as exc:
        raise _translate_error(exc) from exc


@app.get(
    "/v1/continuity/roles",
    dependencies=[Depends(require_token)],
)
def list_roles() -> dict[str, Any]:
    store = get_store()
    return {
        "epoch": store.current_epoch(),
        "leases": store.list_role_leases(),
    }


@app.post(
    "/v1/continuity/tasks",
    dependencies=[Depends(require_token)],
)
def submit_task(request: TaskRequest) -> dict[str, Any]:
    store = get_store()
    try:
        task = store.submit_task(request.model_dump(exclude_none=True))
        _record_internal_event(
            store,
            kind="task.queued",
            payload={
                "task_id": task["task_id"],
                "title": task["title"],
                "kind": task["kind"],
                "priority": task["priority"],
                "required_capabilities": task["required_capabilities"],
            },
            durability="recoverable",
            idempotency_key=f"task.queued:{task['task_id']}",
        )
        return task
    except Exception as exc:
        raise _translate_error(exc) from exc


@app.post(
    "/v1/continuity/tasks/claim",
    dependencies=[Depends(require_token)],
)
def claim_task(request: TaskClaimRequest) -> dict[str, Any]:
    try:
        task = get_store().claim_task(
            node_id=request.node_id,
            capabilities=request.capabilities,
            epoch=request.epoch,
            ttl_ms=request.ttl_ms,
        )
        return {"task": task}
    except Exception as exc:
        raise _translate_error(exc) from exc


@app.post(
    "/v1/continuity/tasks/{task_id}/complete",
    dependencies=[Depends(require_token)],
)
def complete_task(
    task_id: str,
    request: TaskCompleteRequest,
) -> dict[str, Any]:
    store = get_store()
    try:
        task = store.complete_task(
            task_id=task_id,
            node_id=request.node_id,
            claim_token=request.claim_token,
            status=request.status,
            result=request.result,
        )
        result_digest = hashlib.sha256(
            str(task.get("result") or {}).encode()
        ).hexdigest()
        event = _record_internal_event(
            store,
            kind="task.completed",
            payload={
                "task_id": task_id,
                "status": task["state"],
                "completed_at_ms": task["completed_at_ms"],
                "result_digest": result_digest,
            },
            durability="durable",
            idempotency_key=f"task.completed:{task_id}:{task['state']}",
        )
        return {"task": task, "event": event}
    except Exception as exc:
        raise _translate_error(exc) from exc


@app.get(
    "/api/router/backlog-candidates",
    dependencies=[Depends(require_token)],
)
def backlog_candidates(
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    store = get_store()
    tasks = [
        task
        for task in store.snapshot().get("tasks") or []
        if task.get("state") == "queued"
    ]
    return {
        "tasks": tasks[:limit],
        "count": min(len(tasks), limit),
        "epoch": store.current_epoch(),
    }


@app.post(
    "/v1/continuity/context-manifests",
    dependencies=[Depends(require_token)],
)
def put_context_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    store = get_store()
    try:
        record = store.put_context_manifest(manifest)
        event = _record_internal_event(
            store,
            kind="context.manifest.registered",
            payload=record,
            durability=(
                "durable" if record.get("portable") else "recoverable"
            ),
            idempotency_key=f"context.manifest:{record['cache_id']}",
        )
        return {"manifest": record, "event": event}
    except Exception as exc:
        raise _translate_error(exc) from exc


@app.get(
    "/v1/continuity/context-manifests",
    dependencies=[Depends(require_token)],
)
def find_context_manifests(
    prefix_id: str,
    model_id: str,
    scope_id: str,
    compatibility_fingerprint: str = "",
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    values = get_store().find_context_manifests(
        prefix_id=prefix_id,
        model_id=model_id,
        scope_id=scope_id,
        compatibility_fingerprint=compatibility_fingerprint,
        limit=limit,
    )
    return {"manifests": values, "count": len(values)}


@app.get(
    "/v1/continuity/outbox",
    dependencies=[Depends(require_token)],
)
def outbox(
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    events = get_store().pending_durable_events(limit=limit)
    return {"events": events, "count": len(events)}
