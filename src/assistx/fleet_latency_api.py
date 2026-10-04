"""Read-only API projection for fleet latency evidence and shadow placement."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .fleet_latency_shadow import FleetLatencyMapV1, build_shadow_latency_plan


class ShadowPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_family: str = Field(min_length=1, max_length=200)
    candidates: list[dict[str, Any]] = Field(min_length=1)
    expected_prompt_tokens: int = Field(default=512, ge=0, le=1_000_000)
    expected_output_tokens: int = Field(default=128, ge=0, le=1_000_000)
    requires_agent_runtime: bool = False
    requires_code_execution: bool = False


def load_latency_map_file(path: str | Path | None = None) -> FleetLatencyMapV1:
    """Load the current latency map without mutating or regenerating evidence."""

    selected = Path(
        path
        or os.getenv(
            "FLEET_LATENCY_MAP_PATH",
            "artifacts/fleet-latency-map.json",
        )
    )
    try:
        raw = selected.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError("fleet latency map is unavailable") from exc
    try:
        return FleetLatencyMapV1.model_validate_json(raw)
    except ValueError as exc:
        raise ValueError("fleet latency map failed strict validation") from exc


def latency_map_projection(
    document: FleetLatencyMapV1,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the current evidence document plus explicit freshness metadata."""

    now = now or datetime.now(UTC)
    captured = document.captured_at
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=UTC)
    age_seconds = max(0.0, (now.astimezone(UTC) - captured.astimezone(UTC)).total_seconds())
    return {
        "schema": "assistx-fleet-latency-projection-v1",
        "document": document.model_dump(mode="json"),
        "freshness": {
            "captured_at": captured.isoformat(),
            "age_seconds": round(age_seconds, 3),
            "stale_after_seconds": document.stale_after_seconds,
            "document_stale": age_seconds > document.stale_after_seconds,
        },
        "authority": {
            "routing_authority_changed": False,
            "admission_changed": False,
            "dispatch_allowed": False,
            "approval_granted": False,
            "claim_acquired": False,
            "mutation_allowed": False,
        },
    }


def _utcnow() -> datetime:
    return datetime.now(UTC)


def build_fleet_latency_router(
    auth_dependency: Any,
    *,
    loader: Callable[[], FleetLatencyMapV1] = load_latency_map_file,
    clock: Callable[[], datetime] = _utcnow,
) -> APIRouter:
    router = APIRouter(prefix="/api/fleet/latency-map", tags=["fleet-latency"])

    def _load() -> FleetLatencyMapV1:
        try:
            return loader()
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=503,
                detail="Fleet latency evidence unavailable",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail="Fleet latency evidence invalid",
            ) from exc

    @router.get("", dependencies=[Depends(auth_dependency)])
    def get_latency_map() -> dict[str, Any]:
        return latency_map_projection(_load(), now=clock())

    @router.post("/shadow-plan", dependencies=[Depends(auth_dependency)])
    def post_shadow_plan(body: ShadowPlanRequest) -> dict[str, Any]:
        return build_shadow_latency_plan(
            latency_map=_load(),
            candidates=body.candidates,
            task_family=body.task_family,
            expected_prompt_tokens=body.expected_prompt_tokens,
            expected_output_tokens=body.expected_output_tokens,
            requires_agent_runtime=body.requires_agent_runtime,
            requires_code_execution=body.requires_code_execution,
            now=clock(),
        )

    return router
