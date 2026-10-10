"""Research-only authenticated HTTP ingress for strict graph plan admission.

NOT mounted by production FastAPI. Requires a trusted verified-principal
dependency; does not accept a user-supplied subject/body identity or trust
X-Forwarded-User/Tailscale-User-Login from an unverified proxy connection.
No streaming or response-cache promises. Duplicate requests return conflict,
NEVER a replay of previously computed output or a second graph execution.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from trace_graph_entry_guarded import ProtectedGraphEntry
from trace_graph_entry_research import AdmissionDenied
from trace_etcd_quorum_fence_research import FenceRefused


class GraphPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(min_length=1, max_length=64)
    parameters: dict[str, Any]


def build_research_idempotent_graph_router(
    entry: ProtectedGraphEntry, verified_principal_dependency: Callable
) -> APIRouter:
    """Dependency MUST return authenticated principal from trusted ingress.

    It is the deploying application's responsibility to verify Tailnet Serve
    provenance, disallow direct access to spoofed trusted headers, or use
    server-verified credential authentication such as HTTP Basic.
    """
    router = APIRouter(prefix="/_research/graph", tags=["research-only"])

    @router.post("/execute")
    def execute(
        payload: GraphPlanRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        verified_subject: str = Depends(verified_principal_dependency),
    ):
        if not isinstance(verified_subject, str) or not verified_subject:
            raise HTTPException(status_code=401, detail="identity_not_verified")
        if idempotency_key is None:
            raise HTTPException(status_code=428, detail="idempotency_key_required")
        try:
            return {
                "result": entry.execute_authenticated(
                    payload.plan_id, payload.parameters,
                    verified_subject, idempotency_key,
                )
            }
        except AdmissionDenied as exc:
            cause = exc.__cause__
            if isinstance(cause, FenceRefused) and str(cause) in (
                "IDEMPOTENCY_KEY_PAYLOAD_CONFLICT",
                "EXISTING_OPERATION_REQUIRES_RECONCILIATION",
                "CLOSED_OPERATION_REPLAY_DENIED",
            ):
                raise HTTPException(
                    status_code=409,
                    detail="request_already_submitted_or_conflicting",
                ) from None
            if str(exc) in (
                "INVALID_STABLE_REQUEST", "INVALID_QUERY_PARAMETERS",
                "UNREGISTERED_QUERY_PLAN",
            ):
                raise HTTPException(status_code=422,
                                    detail="unapproved_request") from None
            # No automatic retry of uncertain outcome; never re-execute based
            # on a 5xx response, because Raft may already have committed.
            raise HTTPException(status_code=503,
                                detail="execution_state_uncertain") from None

    return router
