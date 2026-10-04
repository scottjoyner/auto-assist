"""Offline regret evidence for fleet latency shadow recommendations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RegretAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routing_authority_changed: Literal[False] = False
    admission_changed: Literal[False] = False
    dispatch_allowed: Literal[False] = False
    approval_granted: Literal[False] = False
    claim_acquired: Literal[False] = False
    mutation_allowed: Literal[False] = False


class CandidateOutcome(BaseModel):
    """Realized or replayed outcome for one exact fleet execution identity."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=500)
    task_family: str = Field(min_length=1, max_length=200)
    node_id: str = Field(min_length=1, max_length=300)
    model_id: str = Field(min_length=1, max_length=500)
    runtime_id: str = Field(min_length=1, max_length=500)
    model_artifact_sha256: str
    quantization: str | None = Field(default=None, max_length=200)
    task_pass: bool
    completion_ms: float = Field(ge=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    evidence_ref: str | None = Field(default=None, max_length=2000)
    replayed: bool = False

    @field_validator("model_artifact_sha256")
    @classmethod
    def validate_artifact_hash(cls, value: str) -> str:
        lowered = value.lower()
        if len(lowered) != 64 or any(ch not in "0123456789abcdef" for ch in lowered):
            raise ValueError("model_artifact_sha256 must be a 64-character hex digest")
        return lowered

    @property
    def handle(self) -> str:
        return (
            f"{self.node_id}/{self.model_id}/{self.runtime_id}/"
            f"{self.model_artifact_sha256}"
        )


def _plan_handle(row: Mapping[str, Any] | None) -> str | None:
    if not row:
        return None
    node_id = str(row.get("node_id") or "")
    model_id = str(row.get("model_id") or "")
    runtime_id = str(row.get("runtime_id") or "")
    artifact = str(row.get("model_artifact_sha256") or "").lower()
    if (
        not node_id
        or not model_id
        or not runtime_id
        or len(artifact) != 64
        or any(ch not in "0123456789abcdef" for ch in artifact)
    ):
        return None
    return f"{node_id}/{model_id}/{runtime_id}/{artifact}"


def _validate_shadow_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema") != "assistx-fleet-latency-shadow-plan-v1":
        raise ValueError("unsupported shadow plan schema")
    if bool(plan.get("executable", False)):
        raise ValueError("shadow plan must be non-executable")
    authority = plan.get("authority")
    if not isinstance(authority, Mapping) or not authority:
        raise ValueError("shadow plan authority block is required")
    if any(bool(value) for value in authority.values()):
        raise ValueError("shadow plan widens authority")
    recommended = plan.get("recommended")
    if recommended is not None:
        if not isinstance(recommended, Mapping) or _plan_handle(recommended) is None:
            raise ValueError(
                "shadow recommendation requires exact node/model/runtime/artifact identity"
            )


def build_latency_regret_evidence(
    *,
    shadow_plan: Mapping[str, Any],
    authoritative_handle: str,
    outcomes: list[CandidateOutcome | dict[str, Any]],
) -> dict[str, Any]:
    """Compare the authoritative route with a replayed shadow recommendation.

    Latency regret is emitted only when both routes produced a passing result.
    A fast failed route therefore cannot appear as a latency improvement.
    """

    _validate_shadow_plan(shadow_plan)
    task_family = str(shadow_plan.get("task_family") or "")
    if not task_family:
        raise ValueError("shadow plan task_family is required")

    parsed = [
        row if isinstance(row, CandidateOutcome) else CandidateOutcome.model_validate(row)
        for row in outcomes
    ]
    if not parsed:
        raise ValueError("at least one candidate outcome is required")

    task_ids = {row.task_id for row in parsed}
    if len(task_ids) != 1:
        raise ValueError("candidate outcomes must belong to one task")
    if any(row.task_family != task_family for row in parsed):
        raise ValueError("candidate outcome task_family does not match shadow plan")

    by_handle: dict[str, CandidateOutcome] = {}
    for row in parsed:
        if row.handle in by_handle:
            raise ValueError(f"duplicate candidate outcome for {row.handle}")
        by_handle[row.handle] = row

    actual = by_handle.get(authoritative_handle)
    if actual is None:
        raise ValueError("authoritative_handle has no outcome evidence")

    shadow_handle = _plan_handle(shadow_plan.get("recommended"))
    shadow = by_handle.get(shadow_handle) if shadow_handle else None

    passing = sorted(
        (row for row in parsed if row.task_pass),
        key=lambda row: (row.completion_ms, row.handle),
    )
    oracle_handles = [row.handle for row in passing]
    oracle_fastest = passing[0].handle if passing else None

    if shadow is None:
        quality_regret = "shadow_outcome_missing"
    elif actual.task_pass and shadow.task_pass:
        quality_regret = "none"
    elif actual.task_pass and not shadow.task_pass:
        quality_regret = "shadow_failed_quality_gate"
    elif not actual.task_pass and shadow.task_pass:
        quality_regret = "authoritative_failed_shadow_passed"
    else:
        quality_regret = "no_passing_comparison"

    latency_regret_ms: float | None = None
    memory_regret_bytes: int | None = None
    if shadow is not None and actual.task_pass and shadow.task_pass:
        latency_regret_ms = round(actual.completion_ms - shadow.completion_ms, 3)
        if (
            actual.peak_memory_bytes is not None
            and shadow.peak_memory_bytes is not None
        ):
            memory_regret_bytes = actual.peak_memory_bytes - shadow.peak_memory_bytes

    estimated_shadow_ms = None
    recommended = shadow_plan.get("recommended")
    if isinstance(recommended, Mapping):
        raw = recommended.get("estimated_time_to_useful_result_ms")
        if raw is not None:
            estimated_shadow_ms = float(raw)

    return {
        "schema": "assistx-fleet-latency-regret-evidence-v1",
        "task_id": actual.task_id,
        "task_family": task_family,
        "origin_node_id": shadow_plan.get("origin_node_id"),
        "actual_authoritative_route": authoritative_handle,
        "actual_model_artifact_sha256": actual.model_artifact_sha256,
        "shadow_latency_route": shadow_handle,
        "shadow_model_artifact_sha256": (
            shadow.model_artifact_sha256 if shadow is not None else None
        ),
        "oracle_passing_handles": oracle_handles,
        "oracle_fastest_passing_handle": oracle_fastest,
        "actual_task_pass": actual.task_pass,
        "shadow_task_pass": shadow.task_pass if shadow is not None else None,
        "actual_completion_ms": actual.completion_ms,
        "shadow_estimated_completion_ms": estimated_shadow_ms,
        "shadow_realized_completion_ms": (
            shadow.completion_ms if shadow is not None else None
        ),
        "quality_regret": quality_regret,
        "latency_regret_ms": latency_regret_ms,
        "memory_regret_bytes": memory_regret_bytes,
        "shadow_latency_improvement_valid": bool(
            shadow is not None
            and actual.task_pass
            and shadow.task_pass
            and latency_regret_ms is not None
            and latency_regret_ms > 0
        ),
        "outcome_count": len(parsed),
        "authority": RegretAuthority().model_dump(mode="json"),
    }
