from __future__ import annotations

from fastapi import APIRouter

from .overlay import build_overlay_health, overlay_endpoints


def build_overlay_router() -> APIRouter:
    router = APIRouter(prefix="/api/overlay", tags=["overlay"])

    @router.get("/status")
    def status() -> dict:
        return build_overlay_health()

    @router.get("/endpoints")
    def endpoints() -> dict:
        return overlay_endpoints()

    return router
