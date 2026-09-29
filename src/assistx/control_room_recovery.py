from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

_LOCK = threading.Lock()
_LAST: dict[str, Any] | None = None
_RECEIVED_AT_MS = 0
STALE_AFTER_MS = 5 * 60 * 1000


def _token() -> str:
    return os.getenv("CONTROL_ROOM_ISLAND_TOKEN", "").strip()


def record_island_heartbeat(payload: dict[str, Any]) -> dict[str, Any]:
    global _LAST, _RECEIVED_AT_MS
    now_ms = int(time.time() * 1000)
    with _LOCK:
        _LAST = payload
        _RECEIVED_AT_MS = now_ms
    return {"ok": True, "received_at_ms": now_ms}


def island_recovery_snapshot() -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    with _LOCK:
        payload = dict(_LAST or {})
        received_at = _RECEIVED_AT_MS
    if not payload:
        return {"present": False, "stale": True, "age_ms": None}
    age = max(0, now_ms - received_at)
    return {
        "present": True,
        "stale": age > STALE_AFTER_MS,
        "age_ms": age,
        "received_at_ms": received_at,
        "payload": payload,
    }


def build_recovery_router(auth_dependency: Callable[..., Any]) -> APIRouter:
    router = APIRouter(tags=["control-room"])

    @router.post("/api/control-room/island-heartbeat")
    def island_heartbeat(
        payload: dict[str, Any],
        x_island_token: str = Header(default=""),
    ) -> dict[str, Any]:
        expected = _token()
        if not expected or x_island_token != expected:
            raise HTTPException(status_code=401, detail="invalid island token")
        if len(json.dumps(payload)) > 64_000:
            raise HTTPException(status_code=413, detail="heartbeat too large")
        return record_island_heartbeat(payload)

    @router.get("/api/control-room/recovery")
    def recovery(_: str = Depends(auth_dependency)) -> dict[str, Any]:
        return island_recovery_snapshot()

    return router


__all__ = [
    "build_recovery_router",
    "island_recovery_snapshot",
    "record_island_heartbeat",
]
