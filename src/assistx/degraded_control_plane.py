from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .deps import load_redis_module
from .operational_journal import AppendOnlyOperationJournal, JournalCorruption
from .operational_state import OperationalRecord, OperationalStateStore
from .runtime_projection import projection_checksum, projection_signature

_DEGRADED_TRUE = {"1", "true", "yes", "on"}
_RUNTIME_LOCK = threading.Lock()
_RUNTIME: "DegradedControlPlaneRuntime | None" = None

_ALLOWED_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/health"): "health",
    ("GET", "/metrics"): "metrics",
    ("GET", "/api/degraded/status"): "status",
    ("GET", "/api/degraded/runtime-projection"): "runtime_projection",
    ("GET", "/api/degraded/context-projection"): "context_projection",
    ("POST", "/api/degraded/runtime-projection/publish"): "publish_projection",
    ("POST", "/api/degraded/heartbeats"): "heartbeats",
    ("POST", "/api/degraded/claims"): "claims",
    ("POST", "/api/degraded/leases"): "leases",
    ("POST", "/api/degraded/delegations/plan"): "delegation",
    ("POST", "/api/degraded/session-context"): "session_context",
    ("POST", "/api/degraded/kv-manifests"): "kv_manifest",
    ("POST", "/api/degraded/recovery-intents"): "recovery_intent",
    ("POST", "/api/degraded/finalizations"): "finalization",
    ("POST", "/api/degraded/primary-return/reconcile"): "primary_return",
    ("POST", "/api/degraded/memory-pressure/evaluate"): "memory_pressure",
    ("POST", "/api/degraded/backup-status"): "backup_status",
}


def degraded_control_plane_enabled() -> bool:
    return os.getenv("ASSISTX_DEGRADED_CONTROL_PLANE", "false").strip().lower() in _DEGRADED_TRUE


def _required_text(value: Any, label: str, max_length: int = 300) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > max_length:
        raise ValueError(f"{label} exceeds {max_length} characters")
    return text


def _required_epoch(value: Any) -> int:
    try:
        epoch = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("epoch must be an integer") from exc
    if epoch <= 0:
        raise ValueError("epoch must be positive")
    return epoch


def _bounded_ttl(value: Any, default: int = 60) -> int:
    try:
        ttl = int(value if value is not None else default)
    except (TypeError, ValueError) as exc:
        raise ValueError("ttl_seconds must be an integer") from exc
    return max(5, min(ttl, 86_400))


def _is_local_provider(provider: Mapping[str, Any]) -> bool:
    quota = str(provider.get("quota_class") or "local").strip().lower()
    provider_type = str(provider.get("type") or "").strip().lower()
    urls = provider.get("access_urls") or [provider.get("base_url")]
    return bool(
        provider.get("enabled", True)
        and quota in {"local", "private"}
        and provider_type in {
            "lmstudio",
            "llama_cpp",
            "vllm",
            "sglang",
            "openai_compatible",
        }
        and all(
            str(url or "").startswith(("http://", "https://"))
            for url in urls
            if url
        )
    )


class DegradedControlPlaneRuntime:
    """Bounded Beelink coordination while Neo4j is absent or restoring."""

    def __init__(
        self,
        store: OperationalStateStore,
        journal: AppendOnlyOperationJournal,
        *,
        neo_factory: Callable[[], Any] | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.store = store
        self.journal = journal
        self.neo_factory = neo_factory
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def status(self) -> dict[str, Any]:
        try:
            journal = self.journal.verify()
        except JournalCorruption as exc:
            journal = {"ok": False, "error": str(exc)}
        mode = self.store.get("control_mode", "fleet")
        projection = self.get_runtime_projection(required=False)
        return {
            "ok": bool(journal.get("ok")),
            "mode": mode.state if mode else "DEGRADED",
            "operational_graph": self.store.graph,
            "journal": journal,
            "runtime_projection": {
                "available": projection is not None,
                "generation": (projection or {}).get("generation"),
                "expires_at_ms": (projection or {}).get("expires_at_ms"),
            },
            "neo4j_final_authority": True,
            "new_durable_writes": "journal_then_neo4j",
        }

    def record_heartbeat(self, body: Mapping[str, Any]) -> OperationalRecord:
        node_id = _required_text(body.get("node_id"), "node_id")
        payload = {
            "runtime_instance_ids": list(body.get("runtime_instance_ids") or []),
            "capabilities": sorted(
                {str(value) for value in body.get("capabilities") or [] if str(value)}
            ),
            "inflight": max(0, int(body.get("inflight") or 0)),
            "memory_available_mb": max(
                0,
                int(body.get("memory_available_mb") or 0),
            ),
            "observed_at_ms": int(body.get("observed_at_ms") or self.clock_ms()),
        }
        return self.store.upsert(
            kind="heartbeat",
            logical_id=node_id,
            state="ONLINE",
            payload=payload,
            ttl_seconds=_bounded_ttl(body.get("ttl_seconds"), 45),
        )

    def acquire_fenced(self, kind: str, body: Mapping[str, Any]) -> OperationalRecord:
        logical_id = _required_text(body.get("logical_id"), "logical_id")
        owner = _required_text(body.get("owner"), "owner")
        return self.store.upsert_fenced(
            kind=kind,
            logical_id=logical_id,
            state=str(body.get("state") or "ACTIVE"),
            owner=owner,
            epoch=_required_epoch(body.get("epoch")),
            payload=dict(body.get("payload") or {}),
            ttl_seconds=_bounded_ttl(body.get("ttl_seconds"), 60),
        )

    def record_unfenced(self, kind: str, body: Mapping[str, Any]) -> OperationalRecord:
        logical_id = _required_text(body.get("logical_id"), "logical_id")
        return self.store.upsert(
            kind=kind,
            logical_id=logical_id,
            state=str(body.get("state") or "ACTIVE"),
            owner=str(body.get("owner") or "") or None,
            epoch=max(0, int(body.get("epoch") or 0)),
            payload=dict(body.get("payload") or {}),
            ttl_seconds=_bounded_ttl(body.get("ttl_seconds"), 300),
        )

    def publish_runtime_projection(
        self,
        document: Mapping[str, Any],
        *,
        secret: str,
    ) -> dict[str, Any]:
        projection = dict(document)
        if not secret:
            raise ValueError("runtime projection HMAC secret is required")
        try:
            generation = int(projection.get("generation") or 0)
            generated_at_ms = int(projection.get("generated_at_ms") or 0)
            expires_at_ms = int(projection.get("expires_at_ms") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("runtime projection timestamps are invalid") from exc
        now = self.clock_ms()
        if generation <= 0 or generated_at_ms > now + 30_000 or expires_at_ms <= now:
            raise ValueError("runtime projection generation or lease is invalid")
        supplied_checksum = str(projection.get("checksum") or "")
        expected_checksum = projection_checksum(projection)
        if not supplied_checksum or supplied_checksum != expected_checksum:
            raise ValueError("runtime projection checksum mismatch")
        supplied_signature = str(projection.get("signature") or "")
        expected_signature = projection_signature(
            generation,
            expected_checksum,
            generated_at_ms,
            expires_at_ms,
            secret,
        )
        if not supplied_signature or supplied_signature != expected_signature:
            raise ValueError("runtime projection signature mismatch")
        providers = projection.get("providers") or []
        if not providers or not all(
            isinstance(provider, dict) and _is_local_provider(provider)
            for provider in providers
        ):
            raise ValueError("degraded projection must contain local providers only")
        ttl_seconds = max(5, min((expires_at_ms - now) // 1000, 900))
        record = self.store.upsert(
            kind="runtime_projection",
            logical_id="canonical",
            state="ACTIVE",
            payload=projection,
            epoch=generation,
            ttl_seconds=ttl_seconds,
        )
        self.journal.append(
            idempotency_key=(
                f"runtime-projection:{generation}:{expected_checksum}"
            ),
            status="COMMITTED",
            payload={"kind": "runtime_projection", "document": projection},
        )
        return record.as_dict()

    def get_runtime_projection(self, *, required: bool = True) -> dict[str, Any] | None:
        record = self.store.get("runtime_projection", "canonical")
        if record:
            document = dict(record.payload)
            if int(document.get("expires_at_ms") or 0) > self.clock_ms():
                return document
        # FalkorDB may be cold after restart while its AOF is loading. The local
        # hash-chained journal retains the last signed projection snapshot.
        for entry in reversed(self.journal.entries()):
            if entry.status != "COMMITTED" or entry.payload.get("kind") != "runtime_projection":
                continue
            document = entry.payload.get("document")
            if isinstance(document, dict) and int(document.get("expires_at_ms") or 0) > self.clock_ms():
                return dict(document)
        if required:
            raise RuntimeError("no fresh signed runtime projection is available")
        return None

    def context_projection(self, base_url: str) -> dict[str, Any]:
        projection = self.get_runtime_projection()
        providers: list[dict[str, Any]] = []
        nodes: list[dict[str, Any]] = []
        services: list[dict[str, Any]] = []
        for provider in projection.get("providers") or []:
            models = provider.get("models") or []
            capabilities = sorted(
                {
                    str(capability)
                    for model in models
                    for capability in model.get("capabilities") or []
                }
                | {"local_only", "privacy"}
            )
            aliases = sorted(
                {str(model.get("alias")) for model in models if model.get("alias")}
            )
            urls = list(provider.get("access_urls") or [provider.get("base_url")])
            node_id = str(provider.get("node_id") or provider.get("runtime_instance_id"))
            provider_id = str(provider.get("name") or provider.get("runtime_instance_id"))
            providers.append(
                {
                    "provider_id": provider_id,
                    "provider": provider_id,
                    "lane": "local",
                    "local": True,
                    "can_use_free_api": False,
                    "blocked": False,
                    "node_id": node_id,
                    "aliases": aliases,
                    "capabilities": capabilities,
                    "runtime_instance_id": provider.get("runtime_instance_id"),
                }
            )
            node_services = []
            for index, url in enumerate(urls):
                service = {
                    "service_id": f"{provider_id}.path.{index}",
                    "name": f"{provider_id} inference path {index}",
                    "url": url,
                    "kind": "local_inference",
                    "node_id": node_id,
                    "provider": provider_id,
                    "status": "approved",
                }
                services.append(service)
                node_services.append(service)
            nodes.append(
                {
                    "node_id": node_id,
                    "display_name": node_id,
                    "lane": "local",
                    "local": True,
                    "running": True,
                    "capabilities": capabilities,
                    "services": node_services,
                }
            )
        return {
            "revision": f"degraded-{projection['generation']}-{projection['checksum'][:12]}",
            "source": "assistx-degraded",
            "generated_at": int(self.clock_ms() / 1000),
            "nodes": nodes,
            "providers": providers,
            "services": services,
            "metadata": {
                "projection_version": "router-context-v1",
                "read_only": True,
                "strict_offline": True,
                "base_url": base_url,
                "durable_authority": "neo4j",
                "operational_store": "falkordb",
            },
        }

    def plan_delegation(self, body: Mapping[str, Any]) -> dict[str, Any]:
        task_id = _required_text(body.get("task_id"), "task_id")
        owner = _required_text(body.get("owner"), "owner")
        epoch = _required_epoch(body.get("epoch"))
        required = {
            str(value)
            for value in body.get("required_capabilities") or []
            if str(value)
        }
        excluded = {
            str(value)
            for value in body.get("excluded_nodes") or []
            if str(value)
        }
        projection = self.get_runtime_projection()
        candidates: list[tuple[int, int, str, dict[str, Any]]] = []
        for provider in projection.get("providers") or []:
            node_id = str(provider.get("node_id") or "")
            if not node_id or node_id in excluded or not _is_local_provider(provider):
                continue
            capabilities = {
                str(capability)
                for model in provider.get("models") or []
                for capability in model.get("capabilities") or []
            }
            if not required.issubset(capabilities):
                continue
            heartbeat = self.store.get("heartbeat", node_id)
            inflight = int((heartbeat.payload if heartbeat else {}).get("inflight") or 0)
            slots = max(1, int(provider.get("parallel_slots") or 1))
            headroom = max(0, slots - inflight)
            healthy = 1 if heartbeat else 0
            if headroom <= 0:
                continue
            candidates.append((healthy, headroom, node_id, provider))
        if not candidates:
            raise RuntimeError("no approved surviving node has delegation headroom")
        _, headroom, node_id, provider = sorted(
            candidates,
            key=lambda item: (-item[0], -item[1], item[2]),
        )[0]
        record = self.store.upsert_fenced(
            kind="delegation",
            logical_id=task_id,
            state="PLANNED",
            owner=owner,
            epoch=epoch,
            ttl_seconds=_bounded_ttl(body.get("ttl_seconds"), 120),
            payload={
                "target_node_id": node_id,
                "runtime_instance_id": provider.get("runtime_instance_id"),
                "provider": provider.get("name"),
                "headroom": headroom,
                "required_capabilities": sorted(required),
                "projection_generation": projection.get("generation"),
                "projection_checksum": projection.get("checksum"),
            },
        )
        return record.as_dict()

    def submit_finalization(self, body: Mapping[str, Any]) -> dict[str, Any]:
        operation_id = _required_text(body.get("operation_id"), "operation_id")
        final_state = _required_text(body.get("final_state"), "final_state").upper()
        payload = {
            "operation_id": operation_id,
            "operation_kind": _required_text(
                body.get("operation_kind"),
                "operation_kind",
            ),
            "final_state": final_state,
            "record_checksum": _required_text(
                body.get("record_checksum"),
                "record_checksum",
            ),
            "epoch": _required_epoch(body.get("epoch")),
            "evidence": dict(body.get("evidence") or {}),
            "requested_at_ms": self.clock_ms(),
        }
        key = "finalize:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.journal.append(
            idempotency_key=key,
            status="PENDING",
            payload=payload,
        )
        if not self.neo_factory or not os.getenv("NEO4J_URI", "").strip():
            return {
                "status": "PENDING_DURABLE_COMMIT",
                "idempotency_key": key,
            }
        result = self.journal.replay(self._neo4j_commit, limit=1)
        return {
            "status": "COMMITTED" if result["committed"] else "PENDING_DURABLE_COMMIT",
            "idempotency_key": key,
            "replay": result,
        }

    def reconcile_primary_return(self, body: Mapping[str, Any]) -> dict[str, Any]:
        owner = _required_text(body.get("owner"), "owner")
        epoch = _required_epoch(body.get("epoch"))
        self.store.upsert_fenced(
            kind="control_mode",
            logical_id="fleet",
            state="DRAINING",
            owner=owner,
            epoch=epoch,
            ttl_seconds=300,
            payload={"reason": "primary_return", "started_at_ms": self.clock_ms()},
        )
        if not self.neo_factory:
            raise RuntimeError("Neo4j factory is unavailable for primary return")
        replay = self.journal.replay(self._neo4j_commit, limit=int(body.get("limit") or 1000))
        if replay["remaining"]:
            return {"status": "DRAINING", "replay": replay}
        final = self.store.upsert_fenced(
            kind="control_mode",
            logical_id="fleet",
            state="RELINQUISHED",
            owner=owner,
            epoch=epoch,
            ttl_seconds=60,
            payload={"completed_at_ms": self.clock_ms(), "replay": replay},
        )
        return {"status": "RELINQUISHED", "record": final.as_dict(), "replay": replay}

    def memory_pressure_plan(self, body: Mapping[str, Any]) -> dict[str, Any]:
        available = max(0, int(body.get("available_mb") or 0))
        total = max(1, int(body.get("total_mb") or 14_336))
        local_model_loaded = bool(body.get("local_model_loaded", True))
        if available >= 2048:
            level = "NORMAL"
            actions: list[str] = []
        elif available >= 1536:
            level = "ELEVATED"
            actions = ["shed_ui_history", "trim_router_cache"]
        elif available >= 1024:
            level = "CRITICAL"
            actions = ["shed_ui_history", "trim_router_cache"]
            if local_model_loaded:
                actions.append("unload_local_model")
        else:
            level = "EMERGENCY"
            actions = [
                "shed_ui_history",
                "trim_router_cache",
                "unload_local_model",
                "block_neo4j_promotion",
                "reject_new_work",
            ]
        record = self.store.upsert(
            kind="memory_pressure",
            logical_id="beelink",
            state=level,
            payload={
                "available_mb": available,
                "total_mb": total,
                "available_ratio": round(available / total, 4),
                "actions": actions,
            },
            ttl_seconds=30,
        )
        return record.as_dict()

    def _neo4j_commit(self, envelope: dict[str, Any]) -> dict[str, Any]:
        if not self.neo_factory:
            raise RuntimeError("Neo4j finalization sink is unavailable")
        neo = self.neo_factory()
        try:
            with neo._session() as session:
                row = session.run(
                    """
                    MERGE (c:DurableOperationCommit {commit_id:$commit_id})
                    ON CREATE SET c.idempotency_key=$idempotency_key,
                                  c.operation_id=$operation_id,
                                  c.operation_kind=$operation_kind,
                                  c.final_state=$final_state,
                                  c.epoch=$epoch,
                                  c.record_checksum=$record_checksum,
                                  c.payload_json=$payload_json,
                                  c.created_at_ts=timestamp()
                    RETURN c.commit_id AS commit_id,
                           c.created_at_ts AS created_at_ts
                    """,
                    {
                        "commit_id": envelope["durable_commit_id"],
                        "idempotency_key": envelope["idempotency_key"],
                        "operation_id": envelope.get("operation_id"),
                        "operation_kind": envelope.get("operation_kind"),
                        "final_state": envelope.get("final_state"),
                        "epoch": envelope.get("epoch"),
                        "record_checksum": envelope.get("record_checksum"),
                        "payload_json": json.dumps(
                            envelope,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                ).single()
            if not row:
                raise RuntimeError("Neo4j durable commit returned no row")
            return {
                "commit_id": row["commit_id"],
                "created_at_ts": row["created_at_ts"],
            }
        finally:
            neo.close()


def build_default_runtime(
    neo_factory: Callable[[], Any] | None = None,
) -> DegradedControlPlaneRuntime:
    global _RUNTIME
    with _RUNTIME_LOCK:
        if _RUNTIME is not None:
            return _RUNTIME
        redis_module = load_redis_module()
        client = redis_module.from_url(
            os.getenv(
                "ASSISTX_OPERATIONAL_STATE_URL",
                "redis://falkordb:6379/0",
            ),
            decode_responses=False,
        )
        store = OperationalStateStore(
            client,
            graph=os.getenv(
                "ASSISTX_OPERATIONAL_STATE_GRAPH",
                "assistx_operational",
            ),
        )
        journal = AppendOnlyOperationJournal(
            Path(
                os.getenv(
                    "ASSISTX_OPERATION_JOURNAL_PATH",
                    "/var/lib/assistx/operation-journal/finalization.jsonl",
                )
            )
        )
        _RUNTIME = DegradedControlPlaneRuntime(
            store,
            journal,
            neo_factory=neo_factory,
        )
        return _RUNTIME


def install_degraded_route_fence(app: Any) -> None:
    if getattr(app.state, "degraded_route_fence_installed", False):
        return
    app.state.degraded_route_fence_installed = True

    @app.middleware("http")
    async def degraded_route_fence(request: Request, call_next):
        if not degraded_control_plane_enabled() or request.method == "OPTIONS":
            return await call_next(request)
        key = (request.method.upper(), request.url.path.rstrip("/") or "/")
        if key not in _ALLOWED_ROUTES:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "route unavailable in degraded control-plane mode",
                    "method": key[0],
                    "path": key[1],
                    "durable_authority": "neo4j",
                    "operational_store": "falkordb",
                },
            )
        return await call_next(request)


def build_degraded_control_router(
    auth_dependency: Any,
    neo_factory: Callable[[], Any] | None = None,
    runtime_factory: Callable[[], DegradedControlPlaneRuntime] | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/degraded",
        tags=["degraded-control-plane"],
        dependencies=[Depends(auth_dependency)],
    )

    def runtime() -> DegradedControlPlaneRuntime:
        return runtime_factory() if runtime_factory else build_default_runtime(neo_factory)

    async def body(request: Request) -> dict[str, Any]:
        try:
            value = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid JSON body") from exc
        if not isinstance(value, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")
        return value

    def invoke(callback: Callable[[], Any]) -> Any:
        try:
            return callback()
        except (ValueError, RuntimeError, JournalCorruption) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/status")
    def status() -> dict[str, Any]:
        return invoke(runtime().status)

    @router.get("/runtime-projection")
    def runtime_projection() -> dict[str, Any]:
        return invoke(runtime().get_runtime_projection)

    @router.get("/context-projection")
    def context_projection(request: Request) -> dict[str, Any]:
        return invoke(
            lambda: runtime().context_projection(str(request.base_url).rstrip("/"))
        )

    @router.post("/runtime-projection/publish")
    async def publish_projection(request: Request) -> dict[str, Any]:
        value = await body(request)
        return invoke(
            lambda: runtime().publish_runtime_projection(
                value,
                secret=os.getenv("ASSISTX_RUNTIME_PROJECTION_HMAC_SECRET", ""),
            )
        )

    @router.post("/heartbeats")
    async def heartbeat(request: Request) -> dict[str, Any]:
        value = await body(request)
        return invoke(lambda: runtime().record_heartbeat(value).as_dict())

    @router.post("/claims")
    async def claim(request: Request) -> dict[str, Any]:
        value = await body(request)
        return invoke(lambda: runtime().acquire_fenced("claim", value).as_dict())

    @router.post("/leases")
    async def lease(request: Request) -> dict[str, Any]:
        value = await body(request)
        return invoke(lambda: runtime().acquire_fenced("lease", value).as_dict())

    @router.post("/delegations/plan")
    async def delegation(request: Request) -> dict[str, Any]:
        value = await body(request)
        return invoke(lambda: runtime().plan_delegation(value))

    @router.post("/session-context")
    async def session_context(request: Request) -> dict[str, Any]:
        value = await body(request)
        return invoke(
            lambda: runtime().record_unfenced("session_context", value).as_dict()
        )

    @router.post("/kv-manifests")
    async def kv_manifest(request: Request) -> dict[str, Any]:
        value = await body(request)
        return invoke(
            lambda: runtime().record_unfenced("kv_manifest", value).as_dict()
        )

    @router.post("/recovery-intents")
    async def recovery_intent(request: Request) -> dict[str, Any]:
        value = await body(request)
        return invoke(
            lambda: runtime().acquire_fenced("recovery_intent", value).as_dict()
        )

    @router.post("/finalizations")
    async def finalization(request: Request) -> dict[str, Any]:
        value = await body(request)
        return invoke(lambda: runtime().submit_finalization(value))

    @router.post("/primary-return/reconcile")
    async def primary_return(request: Request) -> dict[str, Any]:
        value = await body(request)
        return invoke(lambda: runtime().reconcile_primary_return(value))

    @router.post("/memory-pressure/evaluate")
    async def memory_pressure(request: Request) -> dict[str, Any]:
        value = await body(request)
        return invoke(lambda: runtime().memory_pressure_plan(value))

    @router.post("/backup-status")
    async def backup_status(request: Request) -> dict[str, Any]:
        value = await body(request)
        return invoke(
            lambda: runtime().record_unfenced("backup_status", value).as_dict()
        )

    return router
