"""OpenTelemetry-friendly projection of the AssistX normalized trace contract.

The projection is dependency-free and keeps `assistx.trace.v1` canonical. A later
OTLP exporter can translate these records through the OpenTelemetry SDK.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .trace_schema import validate_trace


@dataclass(frozen=True)
class OtelSpanProjection:
    trace_id_hex: str
    span_id_hex: str
    parent_span_id_hex: str | None
    name: str
    attributes: dict[str, Any]


def _hex_id(seed: str, length: int) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:length]


def project_trace_to_otel(trace: dict[str, Any]) -> tuple[OtelSpanProjection, ...]:
    validate_trace(trace)
    trace_id = str(trace["trace_id"])
    trace_id_hex = _hex_id(trace_id, 32)
    projections: list[OtelSpanProjection] = []
    root_span_id = _hex_id(f"{trace_id}:root", 16)
    projections.append(
        OtelSpanProjection(
            trace_id_hex=trace_id_hex,
            span_id_hex=root_span_id,
            parent_span_id_hex=None,
            name="assistx.run",
            attributes={
                "assistx.trace_id": trace_id,
                "assistx.schema_version": trace["schema_version"],
                "assistx.outcome.status": trace["outcome"]["status"],
                "assistx.outcome.evidence_count": len(trace["outcome"].get("evidence_ids", [])),
                "assistx.experiment.name": (trace.get("experiment") or {}).get("name"),
                "assistx.experiment.variant": (trace.get("experiment") or {}).get("variant"),
                "assistx.experiment.manifest_id": (trace.get("experiment") or {}).get("manifest_id"),
            },
        )
    )
    for index, span in enumerate(trace["spans"]):
        span_type = str(span["type"])
        attributes = {
            f"assistx.{key}": value
            for key, value in span.get("attributes", {}).items()
            if value is not None and isinstance(value, (str, int, float, bool))
        }
        attributes["assistx.span.type"] = span_type
        projections.append(
            OtelSpanProjection(
                trace_id_hex=trace_id_hex,
                span_id_hex=_hex_id(f"{trace_id}:{index}:{span_type}", 16),
                parent_span_id_hex=root_span_id,
                name=f"assistx.{span_type}",
                attributes=attributes,
            )
        )
    return tuple(projections)
