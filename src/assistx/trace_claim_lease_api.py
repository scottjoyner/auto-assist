"""Disabled-by-default read-only lease-proof issuer for already claimed tasks.

This endpoint does NOT claim a task or dispatch work. The caller must already
have authenticated Basic/AssistX access AND valid bound node identity.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .trace_claim_lease import IDENT, issue_current_status, issue_lease_proof, load_signer
from .trace_execution_adapter import TraceDenied


class LeaseProofRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=160)
    claim_id: str = Field(min_length=1, max_length=160)
    execution_attempt: int = Field(ge=1)


class CurrentStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_proof: dict[str, Any]
    challenge: str = Field(min_length=64, max_length=64)


def build_claim_lease_router(
    *,
    neo_factory: Callable[[], Any],
    auth_dependency: Any,
    verify_node_identity: Callable[[str, str | None], None],
) -> APIRouter:
    router = APIRouter(prefix="/api/fleet/trace-execution", tags=["trace-execution"])

    @router.post("/claim-lease-proof")
    def claim_lease_proof(
        body: LeaseProofRequest,
        token: str | None = Header(default=None, alias="x-fleet-node-token"),
        _user: str = Depends(auth_dependency),
    ) -> dict[str, Any]:
        # Explicit operator-side configuration; never enabled by deployment.
        if os.getenv("ASSISTX_TRACE_LEASE_ISSUER_ENABLED", "false").lower() not in {
            "true",
            "1",
            "yes",
            "on",
        }:
            raise HTTPException(status_code=503, detail="trace_lease_issuer_disabled")
        verify_node_identity(body.node_id, token)
        path = os.getenv("ASSISTX_TRACE_LEASE_SIGNING_KEY_FILE", "")
        if not path:
            raise HTTPException(status_code=503, detail="claim_signing_key_not_configured")
        try:
            signer = load_signer(Path(path))
            neo = neo_factory()
            # The existing Neo4j Task is authoritative; *not* caller payload.
            task = neo.get_task(body.task_id)
            return {
                "lease_proof": issue_lease_proof(
                    task,
                    task_id=body.task_id,
                    claim_id=body.claim_id,
                    node_id=body.node_id,
                    execution_attempt=body.execution_attempt,
                    signer=signer,
                ),
                "mode": "verify-only-no-executor-activation",
            }
        except TraceDenied as exc:
            # Never return keys, payloads, or internal graph properties.
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/claim-current-status")
    def claim_current_status(
        body: CurrentStatusRequest,
        token: str | None = Header(default=None, alias="x-fleet-node-token"),
        _user: str = Depends(auth_dependency),
    ) -> dict[str, Any]:
        if os.getenv("ASSISTX_TRACE_LEASE_ISSUER_ENABLED", "false").lower() not in {
            "true",
            "1",
            "yes",
            "on",
        }:
            raise HTTPException(status_code=503, detail="trace_lease_issuer_disabled")
        proof = body.lease_proof
        node_id = proof.get("node_id")
        task_id = proof.get("task_id")
        if not isinstance(node_id, str) or not IDENT.fullmatch(node_id):
            raise HTTPException(status_code=400, detail="invalid_node_id")
        if not isinstance(task_id, str) or not IDENT.fullmatch(task_id):
            raise HTTPException(status_code=400, detail="invalid_task_id")
        verify_node_identity(node_id, token)
        path = os.getenv("ASSISTX_TRACE_LEASE_SIGNING_KEY_FILE", "")
        if not path:
            raise HTTPException(status_code=503, detail="claim_signing_key_not_configured")
        try:
            signer = load_signer(Path(path))
            # A *second*, fresh Neo4j read. A cancelled/rotated claim cannot
            # get a new status token, even with a still-valid lease signature.
            neo = neo_factory()
            task = neo.get_task(task_id)
            status = issue_current_status(
                task,
                lease_proof=proof,
                challenge=body.challenge,
                signer=signer,
            )
            return {"current_status": status, "mode": "verify-only-no-executor-activation"}
        except TraceDenied as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
