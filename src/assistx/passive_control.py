from __future__ import annotations

import time
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


VALID_MODES = {"enabled", "paused", "draining", "maintenance"}


class PassiveControlIn(BaseModel):
    mode: str = Field(description="enabled|paused|draining|maintenance")
    reason: Optional[str] = None
    updated_by: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def build_passive_control_router(neo_factory: Callable[[], Any], auth_dependency: Any | None = None) -> APIRouter:
    """Global operator control for passive agent work."""

    dependencies = [Depends(auth_dependency)] if auth_dependency is not None else []
    router = APIRouter(prefix="/api/agents", tags=["passive-control"], dependencies=dependencies)

    @router.get("/passive-control")
    def passive_control_get() -> dict[str, Any]:
        return get_passive_control_state(neo_factory)

    @router.post("/passive-control")
    def passive_control_set(body: PassiveControlIn) -> dict[str, Any]:
        result = set_passive_control_state(neo_factory, body)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result)
        return result

    return router


def get_passive_control_state(neo_factory: Callable[[], Any]) -> dict[str, Any]:
    neo = None
    try:
        neo = neo_factory()
        with neo.driver.session() as s:
            row = s.run(
                """
                MATCH (c:PassiveAgentControl {id:'global'})
                RETURN c
                """
            ).single()
        if not row:
            return default_passive_control_state()
        control = dict(row["c"])
        mode = _normalize_mode(control.get("mode"))
        return {
            "ok": True,
            "mode": mode,
            "passive_allowed": mode == "enabled",
            "new_claims_allowed": mode == "enabled",
            "renewals_allowed": mode in {"enabled", "draining"},
            "recommended_agent_status": recommended_agent_status_for_mode(mode),
            "reason": control.get("reason"),
            "updated_by": control.get("updated_by"),
            "updated_at_ts": control.get("updated_at_ts"),
            "metadata": control.get("metadata") or {},
        }
    except Exception as exc:
        state = default_passive_control_state()
        state["ok"] = False
        state["mode"] = "maintenance"
        state["passive_allowed"] = False
        state["new_claims_allowed"] = False
        state["renewals_allowed"] = False
        state["recommended_agent_status"] = "paused"
        state["error"] = str(exc)[:500]
        return state
    finally:
        try:
            if neo is not None:
                neo.close()
        except Exception:
            pass


def set_passive_control_state(neo_factory: Callable[[], Any], body: PassiveControlIn) -> dict[str, Any]:
    mode = _normalize_mode(body.mode)
    if mode not in VALID_MODES:
        return {"ok": False, "reason": "invalid_mode", "message": f"mode must be one of {sorted(VALID_MODES)}"}
    now_ms = int(time.time() * 1000)
    neo = None
    try:
        neo = neo_factory()
        with neo.driver.session() as s:
            s.run(
                """
                MERGE (c:PassiveAgentControl {id:'global'})
                SET c.mode=$mode,
                    c.reason=$reason,
                    c.updated_by=$updated_by,
                    c.updated_at=datetime(),
                    c.updated_at_ts=$updated_at_ts,
                    c.metadata=$metadata
                """,
                {
                    "mode": mode,
                    "reason": body.reason,
                    "updated_by": body.updated_by,
                    "updated_at_ts": now_ms,
                    "metadata": body.metadata or {},
                },
            ).consume()
    except Exception as exc:
        return {"ok": False, "reason": "neo4j_error", "message": str(exc)[:500]}
    finally:
        try:
            if neo is not None:
                neo.close()
        except Exception:
            pass
    return {
        "ok": True,
        "mode": mode,
        "passive_allowed": mode == "enabled",
        "new_claims_allowed": mode == "enabled",
        "renewals_allowed": mode in {"enabled", "draining"},
        "recommended_agent_status": recommended_agent_status_for_mode(mode),
        "reason": body.reason,
        "updated_by": body.updated_by,
        "updated_at_ts": now_ms,
        "metadata": body.metadata or {},
    }


def default_passive_control_state() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": "enabled",
        "passive_allowed": True,
        "new_claims_allowed": True,
        "renewals_allowed": True,
        "recommended_agent_status": "idle",
        "reason": "default enabled; no PassiveAgentControl node exists yet",
        "updated_by": None,
        "updated_at_ts": None,
        "metadata": {},
    }


def recommended_agent_status_for_mode(mode: str) -> str:
    return {
        "enabled": "idle",
        "paused": "paused",
        "draining": "draining",
        "maintenance": "paused",
    }.get(mode, "paused")


def _normalize_mode(mode: Any) -> str:
    return str(mode or "enabled").strip().lower()
