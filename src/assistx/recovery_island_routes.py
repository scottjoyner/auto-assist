from __future__ import annotations

import hmac
import os
import time
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .recovery_control import (
    Neo4jRecoveryStore,
    RecoveryControlPlane,
    recovery_fingerprint,
)
from .recovery_island import RECOVERY_ISLAND_ACTIONS
from .recovery_mode import recovery_shadow_enabled


class RecoveryIslandRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=200)
    node_id: str = Field(min_length=1, max_length=200)
    deployment: str = Field(min_length=1, max_length=128)
    action: str = Field(pattern="^(stage|verify|activate|deactivate)$")
    reason: str = Field(min_length=3, max_length=2000)
    severity: str = Field(default="critical", pattern="^(info|warning|high|critical)$")
    bundle_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    activation: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _csv_env(name: str) -> set[str]:
    return {
        value.strip()
        for value in os.getenv(name, "").split(",")
        if value.strip()
    }


def _token_authorized(expected: str, supplied: str | None) -> bool:
    if not expected:
        return False
    return bool(supplied) and hmac.compare_digest(expected, supplied)


def _island_plan(body: RecoveryIslandRequestIn) -> dict[str, Any]:
    if body.action in {"stage", "activate"} and not body.bundle_sha256:
        raise ValueError("bundle_sha256 is required for stage and activate")
    island_request: dict[str, Any] = {
        "action": body.action,
        "deployment": body.deployment,
        "node_id": body.node_id,
        "reason": body.reason,
        "severity": body.severity,
        "request_id": body.request_id,
        "metadata": body.metadata,
    }
    if body.bundle_sha256:
        island_request["bundle_sha256"] = body.bundle_sha256
    if body.activation is not None:
        island_request["activation"] = body.activation
    top_level_action = "health_check" if body.action == "verify" else "redeploy_service"
    return {
        "diagnosis_id": f"recovery-island:{body.request_id}",
        "incident_key": f"recovery-island:{body.request_id}",
        "node_id": body.node_id,
        "model_id": None,
        "action": top_level_action,
        "parameters": {"recovery_island": island_request},
        "risk": "high" if body.action == "activate" else "medium",
        "verify_after": ["private_health", "runtime_projection"],
        "rollback": {
            "action": "deactivate",
            "deployment": body.deployment,
        },
    }


def _find_active_duplicate(
    store: Neo4jRecoveryStore,
    fingerprint: str,
) -> dict[str, Any] | None:
    with store.neo._session() as session:
        row = session.run(
            """
            MATCH (p:FleetRecovery {fingerprint:$fingerprint})
            WHERE p.status IN [
              'PROPOSED','APPROVED','EXECUTING','DISPATCHED','VERIFIED'
            ]
            RETURN p.id AS id
            ORDER BY p.created_at_ts DESC
            LIMIT 1
            """,
            {"fingerprint": fingerprint},
        ).single()
    return store.get(str(row["id"])) if row else None


def _auto_approval_allowed(
    body: RecoveryIslandRequestIn,
    *,
    actor: str,
    token_authorized: bool,
) -> tuple[bool, str]:
    if not token_authorized:
        return False, "agent_request_token_not_authorized"
    actors = _csv_env("ASSISTX_RECOVERY_ISLAND_AUTO_APPROVE_ACTORS")
    if actor not in actors:
        return False, "actor_not_auto_approved"
    actions = _csv_env("ASSISTX_RECOVERY_ISLAND_AUTO_APPROVE_ACTIONS")
    if body.action not in actions:
        return False, "action_not_auto_approved"
    if body.action == "activate":
        enabled = os.getenv(
            "ASSISTX_RECOVERY_ISLAND_AUTO_ACTIVATION_ENABLED",
            "false",
        ).lower() in {"1", "true", "yes", "on"}
        if not enabled:
            return False, "automatic_activation_disabled"
        deployments = _csv_env(
            "ASSISTX_RECOVERY_ISLAND_AUTO_ACTIVATION_DEPLOYMENTS"
        ) or {"assistx-shadow"}
        if body.deployment not in deployments:
            return False, "deployment_not_auto_activatable"
    return True, "policy_allowed"


def build_recovery_island_router(
    neo_factory: Callable[[], Any],
    *,
    auth_dependency: Any,
) -> APIRouter:
    router = APIRouter(tags=["recovery-island"])

    @router.get("/api/fleet/recovery-island")
    def recovery_island_status(_user: str = Depends(auth_dependency)):
        neo = neo_factory()
        try:
            proposals = [
                item
                for item in Neo4jRecoveryStore(neo).list(limit=200)
                if isinstance((item.get("plan") or {}).get("parameters"), dict)
                and isinstance(
                    (item.get("plan") or {}).get("parameters", {}).get(
                        "recovery_island"
                    ),
                    dict,
                )
            ]
            return {
                "shadow_mode": recovery_shadow_enabled(),
                "dispatcher_enabled": os.getenv(
                    "ASSISTX_RECOVERY_ISLAND_DISPATCH_ENABLED",
                    "false",
                ).lower()
                in {"1", "true", "yes", "on"},
                "auto_approve_actions": sorted(
                    _csv_env("ASSISTX_RECOVERY_ISLAND_AUTO_APPROVE_ACTIONS")
                ),
                "auto_activation_enabled": os.getenv(
                    "ASSISTX_RECOVERY_ISLAND_AUTO_ACTIVATION_ENABLED",
                    "false",
                ).lower()
                in {"1", "true", "yes", "on"},
                "auto_activation_deployments": sorted(
                    _csv_env(
                        "ASSISTX_RECOVERY_ISLAND_AUTO_ACTIVATION_DEPLOYMENTS"
                    )
                    or {"assistx-shadow"}
                ),
                "proposals": proposals,
            }
        finally:
            neo.close()

    @router.post("/api/fleet/recovery-island/requests")
    def request_recovery_island(
        body: RecoveryIslandRequestIn,
        x_recovery_island_request_token: str | None = Header(None),
        user: str = Depends(auth_dependency),
    ):
        if recovery_shadow_enabled():
            raise HTTPException(
                status_code=409,
                detail="recovery shadow cannot request or approve its own promotion",
            )
        if body.action not in RECOVERY_ISLAND_ACTIONS:
            raise HTTPException(status_code=400, detail="unsupported island action")
        try:
            plan = _island_plan(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        expected_token = os.getenv(
            "ASSISTX_RECOVERY_ISLAND_REQUEST_TOKEN",
            "",
        )
        token_ok = _token_authorized(
            expected_token,
            x_recovery_island_request_token,
        )
        fingerprint = recovery_fingerprint(plan)
        neo = neo_factory()
        try:
            store = Neo4jRecoveryStore(neo)
            duplicate = _find_active_duplicate(store, fingerprint)
            if duplicate:
                return {
                    "created": False,
                    "duplicate": True,
                    "proposal": duplicate,
                    "fingerprint": fingerprint,
                }
            proposal = store.create(
                plan,
                actor=f"requester:{user}",
                fingerprint=fingerprint,
                ttl_seconds=max(
                    300,
                    int(
                        os.getenv(
                            "ASSISTX_RECOVERY_ISLAND_PROPOSAL_TTL_SECONDS",
                            "1800",
                        )
                    ),
                ),
            )
            allowed, policy_reason = _auto_approval_allowed(
                body,
                actor=user,
                token_authorized=token_ok,
            )
            if allowed:
                proposal = RecoveryControlPlane().approve(
                    store,
                    str(proposal["id"]),
                    fingerprint,
                    actor=f"policy:{user}",
                )
            return {
                "created": True,
                "duplicate": False,
                "auto_approved": allowed,
                "policy_reason": policy_reason,
                "proposal": proposal,
                "fingerprint": fingerprint,
                "requested_at_ts": int(time.time() * 1000),
                "dispatch_authority": "recovery-island-dispatcher",
            }
        finally:
            neo.close()

    return router
