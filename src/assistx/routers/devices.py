"""Devices + memory router (W-18 extraction from api.py).

Routes:
  GET  /api/devices
  GET  /api/devices/{device_id}
  POST /api/devices/register
  POST /api/devices/{device_id}/heartbeat
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..api import (
    DeviceHeartbeatIn,
    DeviceRegisterIn,
    auth,
    _neo,
)

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("")
def api_list_devices(
    limit: int = Query(50, ge=1, le=500),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        items = neo.list_agent_devices(limit=limit)
        return {"items": items, "count": len(items)}
    finally:
        neo.close()


@router.get("/{device_id}")
def api_get_device(device_id: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        device = neo.get_agent_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        with neo._session() as s:
            sessions = s.run(
                "MATCH (s:AgentSession) WHERE s.device_id=$did "
                "RETURN s ORDER BY s.updated_at_ts DESC LIMIT 10",
                {"did": device_id},
            )
            agent_sessions = [dict(s["s"]) for s in sessions]
        return {"device": device, "agent_sessions": agent_sessions}
    finally:
        neo.close()


@router.post("/register")
def api_register_device(body: DeviceRegisterIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        device_id = neo.register_device(
            device_id=body.device_id,
            hostname=body.hostname,
            platform=body.platform,
            capabilities=body.capabilities,
            resources=body.resources,
            max_concurrent_tasks=body.max_concurrent_tasks,
            available_agents=body.available_agents,
            tags=body.tags,
        )
        return {"device_id": device_id, "ok": True}
    finally:
        neo.close()


@router.post("/{device_id}/heartbeat")
def api_heartbeat_device(device_id: str, body: DeviceHeartbeatIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        device = neo.heartbeat_device(
            device_id=device_id,
            current_load=body.current_load,
            queue_depth=body.queue_depth,
        )
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        return {"device_id": device_id, "ok": True}
    finally:
        neo.close()


def build_devices_router() -> APIRouter:
    return router
