"""Optional OpenTelemetry SDK emitter for AssistX trace projections.

The OpenTelemetry SDK remains a lazy experiment dependency. AssistX keeps its own
trace schema canonical and emits equivalent spans when an SDK tracer is supplied.
"""

from __future__ import annotations

from typing import Any

from .otel_projection import project_trace_to_otel


def emit_trace_with_otel_sdk(trace: dict[str, Any], *, tracer: Any) -> int:
    """Emit projected AssistX spans using an OpenTelemetry-compatible tracer.

    The AssistX trace ID is retained as an attribute even when the SDK allocates
    transport trace/span IDs. This avoids coupling the canonical trace identity to
    one SDK implementation while preserving correlation in Phoenix/OTLP sinks.
    """
    projections = project_trace_to_otel(trace)
    emitted = 0
    for projection in projections:
        with tracer.start_as_current_span(projection.name) as span:
            span.set_attribute("assistx.canonical_trace_id", trace["trace_id"])
            span.set_attribute("assistx.projected_trace_id_hex", projection.trace_id_hex)
            span.set_attribute("assistx.projected_span_id_hex", projection.span_id_hex)
            if projection.parent_span_id_hex is not None:
                span.set_attribute(
                    "assistx.projected_parent_span_id_hex", projection.parent_span_id_hex
                )
            for key, value in projection.attributes.items():
                if value is not None and isinstance(value, (str, bool, int, float)):
                    span.set_attribute(key, value)
        emitted += 1
    return emitted
