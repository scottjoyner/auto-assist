"""Validated, read-only W3C-to-AssistX TraceGroup projection.

Does NOT write Neo4j, admit a runtime, trust client headers as identity,
log prompt text, or create any new API route. A separately approved backend
worker may pass the returned, explicitly whitelisted kwargs to the existing
swarm_core.record_trace_event after its own admission/authorization checks.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, TypedDict

from .mobile_trace_context import validated_traceparent


class CanonicalTraceRecordKwargs(TypedDict):
    correlation_id: str
    event_type: str
    source: str
    payload: dict[str, str | int]


MobileTraceStage = Literal[
    "gateway_accepted",
    "router_http_accepted",
    "json_response_validated",
    "sse_stream_opened",
    "sse_stream_finished",
    "router_failed",
]

_STAGE_EVENT_TYPE: dict[str, str] = {
    "gateway_accepted": "mobile.model.gateway_accepted",
    "router_http_accepted": "mobile.model.router_http_accepted",
    "json_response_validated": "mobile.model.json_validated",
    "sse_stream_opened": "mobile.model.sse_opened",
    "sse_stream_finished": "mobile.model.sse_finished",
    "router_failed": "mobile.model.router_failed",
}

_CANONICAL_MOBILE_REQUEST = re.compile(r"^kmr:[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class MobileTraceProjection:
    """Immutable, payload-free evidence suitable for a TraceEvent."""

    trace_id: str
    parent_span_id: str
    mobile_request_id: str
    event_type: str
    gateway_http_status: int | None

    def to_trace_record_kwargs(self) -> CanonicalTraceRecordKwargs:
        data: dict[str, str | int] = {
            "schema_version": 1,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "mobile_request_id": self.mobile_request_id,
            "boundary": "assistx-mobile-model",
            "attestation_level": "observational-only",
        }
        if self.gateway_http_status is not None:
            data["gateway_http_status"] = self.gateway_http_status
        return {
            "correlation_id": self.trace_id,
            "event_type": self.event_type,
            "source": "assistx.mobile.model.gateway",
            "payload": data,
        }


def project_mobile_model_trace(
    *,
    traceparent: str | None,
    mobile_request_id: str,
    stage: MobileTraceStage,
    gateway_http_status: int | None = None,
) -> MobileTraceProjection | None:
    """Project only a fully validated receipt into existing graph-trace fields.

    Caller MUST have already authenticated the request, verified Tailnet
    identity/allowlist and admitted the model handle. This is an *offline*
    projection helper, not a bypass of those checks and not a persistence call.

    A 2xx HTTP accept is not equivalent to an SSE completion; stages encode
    those distinct observations explicitly.
    """
    canonical = validated_traceparent(traceparent)
    if canonical is None or _CANONICAL_MOBILE_REQUEST.fullmatch(mobile_request_id) is None:
        return None
    event_type = _STAGE_EVENT_TYPE.get(stage)
    if event_type is None:
        return None
    if gateway_http_status is not None and (
        isinstance(gateway_http_status, bool)
        or not isinstance(gateway_http_status, int)
        or gateway_http_status < 200
        or gateway_http_status > 599
    ):
        return None
    _, trace_id, span_id, _ = canonical.split("-")
    return MobileTraceProjection(
        trace_id=trace_id,
        parent_span_id=span_id,
        mobile_request_id=mobile_request_id,
        event_type=event_type,
        gateway_http_status=gateway_http_status,
    )
