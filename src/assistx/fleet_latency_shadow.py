"""Observation-only fleet latency evidence and shadow placement.

This module deliberately does not import or wrap the authoritative allocation
engine.  It consumes already-qualified candidate rows and produces a shadow
recommendation with all authority fields false.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA = "fleet-latency-map.v1"


class ShadowAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routing_authority_changed: Literal[False] = False
    admission_changed: Literal[False] = False
    dispatch_allowed: Literal[False] = False
    approval_granted: Literal[False] = False
    claim_acquired: Literal[False] = False
    mutation_allowed: Literal[False] = False


class NetworkPathObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin_node_id: str
    target_node_id: str
    transport: str
    access_path: str | None = None
    rtt_ms_p50: float = Field(ge=0)
    rtt_ms_p95: float = Field(ge=0)
    jitter_ms_p95: float = Field(default=0, ge=0)
    loss_rate: float = Field(default=0, ge=0, le=1)
    observed_at: datetime
    samples: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_percentiles(self) -> "NetworkPathObservation":
        if self.rtt_ms_p95 < self.rtt_ms_p50:
            raise ValueError("rtt_ms_p95 must be >= rtt_ms_p50")
        return self


class EndpointLatencyObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    model_id: str
    runtime_id: str
    task_family: str
    model_artifact_sha256: str | None = None
    quantization: str | None = None
    context_bucket: int = Field(default=4096, ge=1)
    concurrency: int = Field(default=1, ge=1)
    warm_state: Literal["warm", "cold", "unknown"] = "unknown"
    measurement_scope: Literal["runtime_local", "end_to_end"] = "runtime_local"
    measurement_origin_node_id: str | None = None
    # For new measurements TTFT means the first generated semantic token:
    # reasoning_content OR ordinary content, whichever arrives first.
    ttft_basis: Literal["reasoning_or_content", "content_only", "unknown"] = "unknown"
    ttft_ms_p50: float = Field(ge=0)
    ttft_ms_p95: float = Field(ge=0)
    first_sse_event_ms_p50: float | None = Field(default=None, ge=0)
    first_sse_event_ms_p95: float | None = Field(default=None, ge=0)
    first_reasoning_ms_p50: float | None = Field(default=None, ge=0)
    first_reasoning_ms_p95: float | None = Field(default=None, ge=0)
    first_content_ms_p50: float | None = Field(default=None, ge=0)
    first_content_ms_p95: float | None = Field(default=None, ge=0)
    prompt_tok_s: float = Field(gt=0)
    decode_tok_s: float = Field(gt=0)
    wall_ms_p50: float = Field(gt=0)
    wall_ms_p95: float = Field(gt=0)
    timeout_rate: float = Field(default=0, ge=0, le=1)
    runtime_success_rate: float = Field(default=1, ge=0, le=1)
    cold_start_ms_p50: float = Field(default=0, ge=0)
    observed_at: datetime
    samples: int = Field(default=1, ge=1)

    @field_validator("model_artifact_sha256")
    @classmethod
    def validate_artifact_hash(cls, value: str | None) -> str | None:
        if value is None:
            return value
        lowered = value.lower()
        if len(lowered) != 64 or any(ch not in "0123456789abcdef" for ch in lowered):
            raise ValueError("model_artifact_sha256 must be a 64-character hex digest")
        return lowered

    @model_validator(mode="after")
    def validate_percentiles(self) -> "EndpointLatencyObservation":
        if self.ttft_ms_p95 < self.ttft_ms_p50:
            raise ValueError("ttft_ms_p95 must be >= ttft_ms_p50")
        if self.wall_ms_p95 < self.wall_ms_p50:
            raise ValueError("wall_ms_p95 must be >= wall_ms_p50")
        for label in ("first_sse_event", "first_reasoning", "first_content"):
            p50 = getattr(self, f"{label}_ms_p50")
            p95 = getattr(self, f"{label}_ms_p95")
            if (p50 is None) != (p95 is None):
                raise ValueError(f"{label} p50/p95 must be supplied together")
            if p50 is not None and p95 is not None and p95 < p50:
                raise ValueError(f"{label}_ms_p95 must be >= {label}_ms_p50")
        if self.measurement_scope == "end_to_end" and not self.measurement_origin_node_id:
            raise ValueError(
                "end_to_end endpoint evidence requires measurement_origin_node_id"
            )
        if (
            self.ttft_basis == "reasoning_or_content"
            and self.first_reasoning_ms_p50 is None
            and self.first_content_ms_p50 is None
        ):
            raise ValueError(
                "reasoning_or_content TTFT requires reasoning or content timing evidence"
            )
        return self


class NodePressureObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    inflight_tasks: int = Field(default=0, ge=0)
    max_concurrent: int = Field(default=1, ge=1)
    queue_depth: int = Field(default=0, ge=0)
    memory_pressure: float = Field(default=0, ge=0, le=1)
    thermal_pressure: float = Field(default=0, ge=0, le=1)
    runtime_warm: bool = False
    captured_at: datetime


class FleetLatencyMapV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal[SCHEMA] = SCHEMA
    captured_at: datetime
    origin_node_id: str
    stale_after_seconds: int = Field(default=300, ge=1)
    network_paths: list[NetworkPathObservation] = Field(default_factory=list)
    endpoints: list[EndpointLatencyObservation] = Field(default_factory=list)
    pressures: list[NodePressureObservation] = Field(default_factory=list)
    authority: ShadowAuthority = Field(default_factory=ShadowAuthority)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _fresh(observed_at: datetime, *, now: datetime, stale_after_seconds: int) -> bool:
    age = (_utc(now) - _utc(observed_at)).total_seconds()
    return 0 <= age <= stale_after_seconds


def _candidate_id(row: dict[str, Any]) -> str:
    return f"{row.get('node_id', '')}/{row.get('model_id', '')}"


def _eligibility_rejection(
    row: dict[str, Any],
    *,
    task_family: str,
    requires_agent_runtime: bool,
    requires_code_execution: bool,
    thermal_hard_stop: float,
) -> str | None:
    if not bool(row.get("online", False)):
        return "offline_or_stale"
    control = str(row.get("control_mode") or "").lower()
    status = str(row.get("status") or "").lower()
    if control in {"maintenance", "quarantined"} or status == "draining":
        return "operator_control"
    if str(row.get("task_family") or "").lower() != task_family.lower():
        return "task_family_mismatch"
    if not bool(row.get("quality_floor_passed", False)):
        return "quality_floor_failed"
    if requires_agent_runtime and not bool(row.get("allow_agent_runtime", False)):
        return "agent_runtime_not_allowed"
    if requires_code_execution and not bool(row.get("allow_code_execution", False)):
        return "code_execution_not_allowed"
    if float(row.get("thermal_pressure") or 0.0) >= thermal_hard_stop:
        return "thermal_hard_stop"
    return None


def _network_for(
    document: FleetLatencyMapV1,
    *,
    target_node_id: str,
    now: datetime,
) -> NetworkPathObservation | None:
    rows = [
        row
        for row in document.network_paths
        if row.origin_node_id == document.origin_node_id
        and row.target_node_id == target_node_id
        and _fresh(
            row.observed_at,
            now=now,
            stale_after_seconds=document.stale_after_seconds,
        )
    ]
    if not rows:
        return None
    return min(rows, key=lambda row: (row.rtt_ms_p95, row.loss_rate, row.transport))


def _endpoint_for(
    document: FleetLatencyMapV1,
    *,
    node_id: str,
    model_id: str,
    task_family: str,
    runtime_id: str | None,
    model_artifact_sha256: str | None,
    quantization: str | None,
    now: datetime,
) -> EndpointLatencyObservation | None:
    rows = [
        row
        for row in document.endpoints
        if row.node_id == node_id
        and row.model_id == model_id
        and row.task_family.lower() == task_family.lower()
        and (runtime_id is None or row.runtime_id == runtime_id)
        and (
            model_artifact_sha256 is None
            or row.model_artifact_sha256 == model_artifact_sha256.lower()
        )
        and (quantization is None or row.quantization == quantization)
        and (
            row.measurement_scope != "end_to_end"
            or row.measurement_origin_node_id == document.origin_node_id
        )
        and _fresh(
            row.observed_at,
            now=now,
            stale_after_seconds=document.stale_after_seconds,
        )
    ]
    if not rows:
        return None
    return min(rows, key=lambda row: (row.wall_ms_p95, row.timeout_rate))


def _pressure_for(
    document: FleetLatencyMapV1,
    *,
    node_id: str,
    now: datetime,
) -> NodePressureObservation | None:
    rows = [
        row
        for row in document.pressures
        if row.node_id == node_id
        and _fresh(
            row.captured_at,
            now=now,
            stale_after_seconds=document.stale_after_seconds,
        )
    ]
    if not rows:
        return None
    return max(rows, key=lambda row: _utc(row.captured_at))


def estimate_time_to_useful_result_ms(
    *,
    endpoint: EndpointLatencyObservation,
    network: NetworkPathObservation,
    pressure: NodePressureObservation | None,
    expected_prompt_tokens: int,
    expected_output_tokens: int,
) -> dict[str, float]:
    """Return an explainable end-to-end latency estimate.

    TTFT normally already contains prompt processing, so prompt processing is
    used as a floor rather than added a second time.
    """

    prompt_ms = max(0.0, expected_prompt_tokens / endpoint.prompt_tok_s * 1000.0)
    first_token_ms = max(endpoint.ttft_ms_p50, prompt_ms)
    decode_ms = max(0.0, expected_output_tokens / endpoint.decode_tok_s * 1000.0)

    queue_ms = 0.0
    warm_penalty_ms = 0.0
    if pressure is not None:
        queue_batches = pressure.queue_depth / max(pressure.max_concurrent, 1)
        saturated = max(
            0.0,
            (pressure.inflight_tasks - pressure.max_concurrent + 1)
            / max(pressure.max_concurrent, 1),
        )
        queue_ms = endpoint.wall_ms_p50 * (queue_batches + saturated)
        if not pressure.runtime_warm:
            warm_penalty_ms = endpoint.cold_start_ms_p50
    elif endpoint.warm_state == "cold":
        warm_penalty_ms = endpoint.cold_start_ms_p50

    # End-to-end endpoint timing already includes transport from its declared
    # origin. Runtime-local timing does not, so only then add the network path.
    network_ms = (
        0.0 if endpoint.measurement_scope == "end_to_end" else network.rtt_ms_p95
    )
    total = network_ms + queue_ms + warm_penalty_ms + first_token_ms + decode_ms

    return {
        "network_ms": round(network_ms, 3),
        "queue_delay_ms": round(queue_ms, 3),
        "warm_penalty_ms": round(warm_penalty_ms, 3),
        "prompt_ms": round(prompt_ms, 3),
        "first_token_ms": round(first_token_ms, 3),
        "decode_ms": round(decode_ms, 3),
        "estimated_time_to_useful_result_ms": round(total, 3),
    }


def build_shadow_latency_plan(
    *,
    latency_map: FleetLatencyMapV1 | dict[str, Any],
    candidates: list[dict[str, Any]],
    task_family: str,
    expected_prompt_tokens: int = 512,
    expected_output_tokens: int = 128,
    requires_agent_runtime: bool = False,
    requires_code_execution: bool = False,
    now: datetime | None = None,
    thermal_hard_stop: float = 0.95,
) -> dict[str, Any]:
    """Rank already-known candidates by fresh end-to-end latency evidence.

    This function never dispatches.  Qualification and operator restrictions
    remain hard gates; latency is only an optimization among candidates that
    survive them.
    """

    document = (
        latency_map
        if isinstance(latency_map, FleetLatencyMapV1)
        else FleetLatencyMapV1.model_validate(latency_map)
    )
    now = now or datetime.now(UTC)

    ranked: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []

    for row in candidates:
        node_id = str(row.get("node_id") or "")
        model_id = str(row.get("model_id") or "")
        identity = _candidate_id(row)
        rejection = _eligibility_rejection(
            row,
            task_family=task_family,
            requires_agent_runtime=requires_agent_runtime,
            requires_code_execution=requires_code_execution,
            thermal_hard_stop=thermal_hard_stop,
        )
        if rejection is not None:
            rejected.append({"candidate": identity, "reason": rejection})
            continue

        network = _network_for(document, target_node_id=node_id, now=now)
        endpoint = _endpoint_for(
            document,
            node_id=node_id,
            model_id=model_id,
            task_family=task_family,
            runtime_id=(
                str(row.get("runtime_id")) if row.get("runtime_id") is not None else None
            ),
            model_artifact_sha256=(
                str(row.get("model_artifact_sha256"))
                if row.get("model_artifact_sha256") is not None
                else None
            ),
            quantization=(
                str(row.get("quantization"))
                if row.get("quantization") is not None
                else None
            ),
            now=now,
        )
        if network is None or endpoint is None:
            rejected.append({"candidate": identity, "reason": "missing_fresh_latency_evidence"})
            continue

        pressure = _pressure_for(document, node_id=node_id, now=now)
        merged_thermal = max(
            float(row.get("thermal_pressure") or 0.0),
            pressure.thermal_pressure if pressure is not None else 0.0,
        )
        if merged_thermal >= thermal_hard_stop:
            rejected.append({"candidate": identity, "reason": "thermal_hard_stop"})
            continue

        estimate = estimate_time_to_useful_result_ms(
            endpoint=endpoint,
            network=network,
            pressure=pressure,
            expected_prompt_tokens=max(0, int(expected_prompt_tokens)),
            expected_output_tokens=max(0, int(expected_output_tokens)),
        )
        ranked.append(
            {
                "node_id": node_id,
                "model_id": model_id,
                "task_family": task_family,
                "quality_score": float(row.get("quality_score") or 0.0),
                "quality_confidence": float(row.get("quality_confidence") or 0.0),
                "runtime_id": endpoint.runtime_id,
                "model_artifact_sha256": endpoint.model_artifact_sha256,
                "quantization": endpoint.quantization,
                "measurement_scope": endpoint.measurement_scope,
                "measurement_origin_node_id": endpoint.measurement_origin_node_id,
                "ttft_basis": endpoint.ttft_basis,
                "first_sse_event_ms_p50": endpoint.first_sse_event_ms_p50,
                "first_reasoning_ms_p50": endpoint.first_reasoning_ms_p50,
                "first_content_ms_p50": endpoint.first_content_ms_p50,
                "transport": network.transport,
                "runtime_warm": (
                    pressure.runtime_warm if pressure is not None else endpoint.warm_state == "warm"
                ),
                "thermal_pressure": round(merged_thermal, 3),
                **estimate,
            }
        )

    ranked.sort(
        key=lambda row: (
            row["estimated_time_to_useful_result_ms"],
            -row["quality_score"],
            -row["quality_confidence"],
            row["node_id"],
            row["model_id"],
        )
    )
    recommended = ranked[0] if ranked else None

    return {
        "schema": "assistx-fleet-latency-shadow-plan-v1",
        "origin_node_id": document.origin_node_id,
        "task_family": task_family,
        "recommended": recommended,
        "alternatives": ranked[1:4],
        "rejected": rejected,
        "executable": False,
        "authority": ShadowAuthority().model_dump(mode="json"),
        "policy": {
            "qualification_before_latency": True,
            "fresh_evidence_required": True,
            "automatic_dispatch": False,
            "thermal_hard_stop": thermal_hard_stop,
        },
    }
