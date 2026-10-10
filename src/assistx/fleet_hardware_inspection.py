"""Opt-in, post-hoc hardware inspection receipts attached to existing traces.

Not an execution/decision event: this NEVER calls swarm_core.record_trace_event
(which changes TraceGroup.current_state), creates a TraceGroup, schedules jobs,
or accesses private device contents beyond the bounded read-only preview.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .fleet_hardware_preview import FleetHardwareEvidenceError, hardware_preview

_CORRELATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_INSPECTION_INTENT = "fleet-hardware-inspection-v1"
_MAX_PER_TRACE = 20


class InspectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ram_gib: float = Field(default=0.0, ge=0, le=4096)
    gpu_vram_gib: float = Field(default=0.0, ge=0, le=4096)
    data_host: str | None = Field(default=None, max_length=80)
    confirm_post_hoc_observation: bool

    @field_validator("ram_gib", "gpu_vram_gib")
    @classmethod
    def finite_capacity(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("capacity must be finite")
        return v


def prepare_inspection_receipt(correlation_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """Pure deterministic, sanitized receipt; no raw hardware source copied."""
    if not _CORRELATION.fullmatch(correlation_id):
        raise ValueError("invalid correlation ID")
    if evidence.get("snapshot_freshness") != "FRESH":
        raise ValueError("stale evidence cannot be recorded as a current inspection")
    if evidence.get("dispatch_allowed") is not False or evidence.get("admission_allowed") is not False:
        raise ValueError("evidence must explicitly deny dispatch and admission")
    digest = evidence.get("evidence_digest_sha256")
    snapshot = evidence.get("snapshot")
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise ValueError("invalid source digest")
    if not isinstance(snapshot, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z", snapshot):
        raise ValueError("invalid snapshot ID")
    requirements = evidence.get("requirements")
    if not isinstance(requirements, dict):
        raise ValueError("no capacity requirements available")
    ram = requirements.get("ram_gib")
    gpu = requirements.get("gpu_vram_gib")
    host = requirements.get("data_host")
    if any(
        isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0 or x > 4096
        for x in (ram, gpu)
    ):
        raise ValueError("invalid capacity request")
    if host is not None and (not isinstance(host, str) or not re.fullmatch("[a-z0-9][a-z0-9-]{0,79}", host)):
        raise ValueError("invalid data-host identity")
    nodes = evidence.get("nodes")
    if not isinstance(nodes, list) or any(
        not isinstance(n, dict) or n.get("dispatch") != "DENIED_NO_AUTHORITY" for n in nodes
    ):
        raise ValueError("candidate evidence must deny dispatch")
    # The preview is capped at 50; never mistake a truncated count as complete.
    key = {
        "schema_version": 1,
        "kind": "POST_HOC_OPERATOR_INSPECTION",
        "correlation_id": correlation_id,
        "snapshot": snapshot,
        "evidence_digest_sha256": digest,
        "requirements": {"ram_gib": float(ram), "gpu_vram_gib": float(gpu), "data_host": host},
    }
    token = hashlib.sha256(json.dumps(key, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {
        **key,
        "receipt_id": "fleet-hardware-inspection:" + token,
        "visible_nodes": len(nodes),
        "total_registered_nodes": evidence.get("checked_nodes"),
        "evidence_truncated": evidence.get("truncated") is True,
        "admission_allowed": False,
        "dispatch_allowed": False,
        "historical_decision_proof": False,
    }


def persist_inspection(neo: Any, receipt: dict[str, Any], *, authenticated_actor: str) -> dict[str, Any] | None:
    """One bounded idempotent graph write to an EXISTING TraceGroup only.

    The Cypher does not mention :Task, :TraceEvent, current_state, state, or
    agent reservations. A 21st distinct inspection is refused for each trace.
    """
    actor = authenticated_actor[:80]
    if not actor or not re.fullmatch(r"[\w.@+-]{1,80}", actor, flags=re.ASCII):
        raise ValueError("invalid authenticated actor")
    parameters = {
        "correlation_id": receipt["correlation_id"],
        "receipt_id": receipt["receipt_id"],
        "snapshot": receipt["snapshot"],
        "evidence_digest_sha256": receipt["evidence_digest_sha256"],
        "requirements_json": json.dumps(receipt["requirements"], sort_keys=True, allow_nan=False),
        "mode": receipt["kind"],
        "actor": actor,
        "max_receipts": _MAX_PER_TRACE,
        "visible_nodes": receipt["visible_nodes"],
        "evidence_truncated": receipt["evidence_truncated"],
    }
    query = """
        MATCH (g:TraceGroup {correlation_id:$correlation_id})
        OPTIONAL MATCH (g)-[:HAS_HARDWARE_INSPECTION]->(older:FleetHardwareInspection)
        WITH g, collect(older.receipt_id) AS existing_ids
        WHERE size(existing_ids) < $max_receipts OR $receipt_id IN existing_ids
        MERGE (r:FleetHardwareInspection {receipt_id:$receipt_id})
        ON CREATE SET
            r.correlation_id=$correlation_id,
            r.snapshot=$snapshot,
            r.evidence_digest_sha256=$evidence_digest_sha256,
            r.requirements_json=$requirements_json,
            r.kind=$mode,
            r.actor=$actor,
            r.visible_nodes=$visible_nodes,
            r.evidence_truncated=$evidence_truncated,
            r.admission_allowed=false,
            r.dispatch_allowed=false,
            r.historical_decision_proof=false,
            r.recorded_at=datetime(),
            r.recorded_at_ms=timestamp()
        MERGE (g)-[:HAS_HARDWARE_INSPECTION]->(r)
        RETURN r.receipt_id AS receipt_id, r.recorded_at_ms AS recorded_at_ms
    """
    with neo._session() as session:
        row = session.run(query, parameters).single()
        if row is None:
            return None
        return {"receipt_id": row["receipt_id"], "recorded_at_ms": row["recorded_at_ms"]}


def list_inspections(neo: Any, correlation_id: str) -> list[dict[str, Any]]:
    if not _CORRELATION.fullmatch(correlation_id):
        raise ValueError("invalid correlation ID")
    with neo._session() as session:
        rows = session.run(
            """
            MATCH (g:TraceGroup {correlation_id:$correlation_id})
                  -[:HAS_HARDWARE_INSPECTION]->(r:FleetHardwareInspection)
            RETURN r.receipt_id AS receipt_id, r.snapshot AS snapshot,
                   r.evidence_digest_sha256 AS evidence_digest_sha256,
                   r.requirements_json AS requirements_json,
                   r.actor AS actor,
                   r.visible_nodes AS visible_nodes,
                   r.evidence_truncated AS evidence_truncated,
                   r.recorded_at_ms AS recorded_at_ms,
                   r.kind AS kind
            ORDER BY r.recorded_at_ms DESC
            LIMIT $limit
            """,
            {"correlation_id": correlation_id, "limit": _MAX_PER_TRACE},
        )
        output = []
        for row in rows:
            output.append(
                {
                    "receipt_id": row["receipt_id"],
                    "snapshot": row["snapshot"],
                    "evidence_digest_sha256": row["evidence_digest_sha256"],
                    "requirements": json.loads(row["requirements_json"]),
                    "actor": row["actor"],
                    "visible_nodes": row["visible_nodes"],
                    "evidence_truncated": row["evidence_truncated"],
                    "recorded_at_ms": row["recorded_at_ms"],
                    "kind": row["kind"],
                    "historical_decision_proof": False,
                    "admission_allowed": False,
                    "dispatch_allowed": False,
                }
            )
        return output


def build_inspection_router(
    *,
    auth_dependency: Callable[..., Any],
    neo_factory: Callable[[], Any],
    root_provider: Callable[[], str] | None = None,
    write_enabled: Callable[[], bool] | None = None,
) -> APIRouter:
    path_provider = root_provider or (lambda: os.getenv("ASSISTX_FLEET_HARDWARE_ROOT", "").strip())
    allow_write = write_enabled or (lambda: os.getenv("ASSISTX_FLEET_HARDWARE_INSPECTION_WRITES") == "1")
    router = APIRouter(prefix="/api/fleet/trace-inspections", tags=["fleet-hardware-inspection"])

    @router.get("/{correlation_id}")
    def read_receipts(correlation_id: str, _: str = Depends(auth_dependency)) -> dict[str, Any]:
        if not _CORRELATION.fullmatch(correlation_id):
            raise HTTPException(status_code=422, detail="invalid correlation ID")
        db = neo_factory()
        try:
            items = list_inspections(db, correlation_id)
            return {
                "correlation_id": correlation_id,
                "kind": "POST_HOC_OPERATOR_INSPECTIONS",
                "admission_allowed": False,
                "dispatch_allowed": False,
                "receipts": items,
            }
        finally:
            db.close()

    @router.post("/{correlation_id}")
    def record_receipt(
        correlation_id: str,
        body: InspectionInput = Body(...),
        x_assistx_operator_intent: str | None = Header(None),
        actor: str = Depends(auth_dependency),
    ) -> dict[str, Any]:
        if not allow_write():
            raise HTTPException(status_code=503, detail="hardware inspection recording disabled")
        if x_assistx_operator_intent != _INSPECTION_INTENT or body.confirm_post_hoc_observation is not True:
            raise HTTPException(status_code=403, detail="explicit operator intent required")
        if not _CORRELATION.fullmatch(correlation_id):
            raise HTTPException(status_code=422, detail="invalid correlation ID")
        location = path_provider()
        if not location or not Path(location).is_absolute():
            raise HTTPException(status_code=503, detail="private hardware evidence unavailable")
        try:
            preview = hardware_preview(
                Path(location),
                min_ram_gib=body.ram_gib,
                min_gpu_vram_gib=body.gpu_vram_gib,
                data_host=body.data_host,
                limit=50,
            )
            receipt = prepare_inspection_receipt(correlation_id, preview)
            db = neo_factory()
            try:
                result = persist_inspection(db, receipt, authenticated_actor=actor)
            finally:
                db.close()
        except (FleetHardwareEvidenceError, OSError, TypeError) as exc:
            raise HTTPException(status_code=503, detail="inspection evidence unavailable") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=409, detail="trace unavailable or inspection limit reached")
        return {
            "kind": receipt["kind"],
            "snapshot": receipt["snapshot"],
            "evidence_digest_sha256": receipt["evidence_digest_sha256"],
            "requirements": receipt["requirements"],
            "receipt_id": result["receipt_id"],
            "recorded_at_ms": result["recorded_at_ms"],
            "historical_decision_proof": False,
            "admission_allowed": False,
            "dispatch_allowed": False,
        }

    return router
