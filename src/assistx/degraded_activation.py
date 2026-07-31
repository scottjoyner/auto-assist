from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .degraded_control_plane import (
    _ALLOWED_ROUTES,
    DegradedControlPlaneRuntime,
    build_default_runtime,
    degraded_control_plane_enabled,
)
from .recovery_island import verify_recovery_activation

_ALLOWED_ROUTES[("POST", "/api/degraded/activate")] = "activate"
_ALLOWED_ROUTES[("POST", "/api/degraded/events")] = "events"

_ACTIVE_REQUIRED = {
    ("POST", "/api/degraded/claims"),
    ("POST", "/api/degraded/leases"),
    ("POST", "/api/degraded/delegations/plan"),
    ("POST", "/api/degraded/session-context"),
    ("POST", "/api/degraded/kv-manifests"),
    ("POST", "/api/degraded/recovery-intents"),
    ("POST", "/api/degraded/finalizations"),
    ("POST", "/api/degraded/primary-return/reconcile"),
}


def _json_mapping(value: str) -> dict[str, str]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {
        str(key): str(secret)
        for key, secret in decoded.items()
        if str(key) and str(secret)
    }


def _claim_nonce(nonce: str) -> None:
    root = Path(
        os.getenv(
            "ASSISTX_DEGRADED_ACTIVATION_NONCE_DIR",
            "/var/lib/assistx/operation-journal/activation-nonces",
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    path = root / hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as exc:
        raise RuntimeError("degraded activation replay detected") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(str(int(time.time())) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def degraded_is_active(runtime: DegradedControlPlaneRuntime) -> bool:
    record = runtime.store.get("control_mode", "fleet")
    return bool(record and record.state == "DEGRADED_ACTIVE")


def install_degraded_activation_fence(
    app: Any,
    runtime_factory: Callable[[], DegradedControlPlaneRuntime],
) -> None:
    if getattr(app.state, "degraded_activation_fence_installed", False):
        return
    app.state.degraded_activation_fence_installed = True

    @app.middleware("http")
    async def degraded_activation_fence(request: Request, call_next):
        if not degraded_control_plane_enabled():
            return await call_next(request)
        key = (request.method.upper(), request.url.path.rstrip("/") or "/")
        if key in _ACTIVE_REQUIRED and not degraded_is_active(runtime_factory()):
            return JSONResponse(
                status_code=423,
                content={
                    "detail": "degraded control plane is warm but not activated",
                    "required_state": "DEGRADED_ACTIVE",
                    "path": key[1],
                },
            )
        return await call_next(request)


def build_degraded_activation_router(
    auth_dependency: Any,
    runtime_factory: Callable[[], DegradedControlPlaneRuntime] | None = None,
    neo_factory: Callable[[], Any] | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/degraded",
        tags=["degraded-control-plane"],
        dependencies=[Depends(auth_dependency)],
    )

    def runtime() -> DegradedControlPlaneRuntime:
        return runtime_factory() if runtime_factory else build_default_runtime(neo_factory)

    @router.post("/activate")
    async def activate(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid JSON body") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")
        envelope = body.get("activation")
        if not isinstance(envelope, dict):
            raise HTTPException(status_code=409, detail="signed activation is required")
        keys = _json_mapping(
            os.getenv(
                "ASSISTX_RECOVERY_ACTIVATION_VERIFY_KEYS",
                os.getenv("FLEET_RECOVERY_ACTIVATION_VERIFY_KEYS", "{}"),
            )
        )
        target_node_id = os.getenv("FLEET_NODE_ID", "beelink-recovery")
        deployment = os.getenv(
            "ASSISTX_DEGRADED_DEPLOYMENT_NAME",
            "assistx-degraded",
        )
        bundle_sha256 = os.getenv("ASSISTX_RECOVERY_BUNDLE_SHA256", "")
        current = runtime().store.get("control_mode", "fleet")
        minimum_epoch = current.epoch if current else 0
        error = verify_recovery_activation(
            envelope,
            keys,
            node_id=target_node_id,
            deployment=deployment,
            bundle_sha256=bundle_sha256,
            minimum_epoch=minimum_epoch,
        )
        if error:
            raise HTTPException(status_code=409, detail=error)
        nonce = str((envelope.get("attestation") or {}).get("nonce") or "")
        try:
            _claim_nonce(nonce)
            expires_at = int((envelope.get("attestation") or {})["expires_at"])
            now = int(time.time())
            ttl_seconds = max(30, min(expires_at - now, 3600))
            record = runtime().store.upsert_fenced(
                kind="control_mode",
                logical_id="fleet",
                state="DEGRADED_ACTIVE",
                owner=str(envelope.get("fence_proof") or ""),
                epoch=int(envelope.get("epoch") or 0),
                ttl_seconds=ttl_seconds,
                payload={
                    "activation_key_id": str(
                        (envelope.get("attestation") or {}).get("key_id") or ""
                    ),
                    "bundle_sha256": bundle_sha256,
                    "activated_at_ms": int(time.time() * 1000),
                },
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "ok": True,
            "status": "DEGRADED_ACTIVE",
            "record": record.as_dict(),
        }

    @router.post("/events")
    async def events(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid JSON body") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")
        identity = str(
            body.get("event_id")
            or body.get("request_id")
            or hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        record = runtime().store.upsert(
            kind="route_observation",
            logical_id=identity,
            state=str(body.get("status") or "OBSERVED"),
            payload=body,
            ttl_seconds=max(30, min(int(body.get("ttl_seconds") or 3600), 86_400)),
        )
        return {"ok": True, "record_id": record.record_id}

    return router
