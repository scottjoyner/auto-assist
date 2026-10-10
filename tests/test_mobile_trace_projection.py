from __future__ import annotations

from assistx.mobile_trace_projection import project_mobile_model_trace


VALID_TRACE = "00-1234567890abcdef1234567890abcdef-0123456789abcdef-01"
VALID_REQUEST_ID = "kmr:5d1c3326c30b47daa24329d456773f3e"


def test_tracegroup_projection_has_existing_record_trace_event_kwargs():
    item = project_mobile_model_trace(
        traceparent=VALID_TRACE,
        mobile_request_id=VALID_REQUEST_ID,
        stage="router_http_accepted",
        gateway_http_status=200,
    )
    assert item is not None
    params = item.to_trace_record_kwargs()
    assert set(params) == {"correlation_id", "event_type", "source", "payload"}
    assert params["correlation_id"] == "1234567890abcdef1234567890abcdef"
    assert params["event_type"] == "mobile.model.router_http_accepted"
    assert params["payload"]["parent_span_id"] == "0123456789abcdef"
    assert params["payload"]["mobile_request_id"] == VALID_REQUEST_ID
    assert params["payload"]["attestation_level"] == "observational-only"
    assert "Authorization" not in str(params)
    assert "prompt" not in str(params)


def test_request_id_matches_real_mobile_gateway_receipt_contract():
    for invalid in (
        "kmr:0123", "kmr:" + "a" * 33, "kmr:" + "Z" * 32,
        "Bearer mysecret", "kmr:" + "z" * 32,
        VALID_REQUEST_ID + "\nAuthorization: Bearer value",
    ):
        assert project_mobile_model_trace(
            traceparent=VALID_TRACE, mobile_request_id=invalid,
            stage="router_http_accepted",
        ) is None


def test_spoofed_invalid_traceparent_is_never_projected():
    for invalid in (
        "", None, "01-" + VALID_TRACE[3:],
        "00-" + ("0" * 32) + VALID_TRACE[35:],
        VALID_TRACE + "BAD", VALID_TRACE.upper(),
    ):
        assert project_mobile_model_trace(
            traceparent=invalid, mobile_request_id=VALID_REQUEST_ID,
            stage="gateway_accepted",
        ) is None


def test_stage_semantics_are_distinct_and_bounded():
    stages = (
        "gateway_accepted", "router_http_accepted", "json_response_validated",
        "sse_stream_opened", "sse_stream_finished", "router_failed",
    )
    projections = [
        project_mobile_model_trace(
            traceparent=VALID_TRACE, mobile_request_id=VALID_REQUEST_ID,
            stage=stage,
        ) for stage in stages
    ]
    assert all(item is not None for item in projections)
    assert len({item.event_type for item in projections if item is not None}) == len(stages)
    assert project_mobile_model_trace(
        traceparent=VALID_TRACE, mobile_request_id=VALID_REQUEST_ID,
        stage="agent_auto_authorized",  # type: ignore[arg-type]
    ) is None


def test_status_cannot_encode_unbounded_or_sensitive_metadata():
    for bad in (0, 199, 600, 9000, True, "200", -100):
        assert project_mobile_model_trace(
            traceparent=VALID_TRACE, mobile_request_id=VALID_REQUEST_ID,
            stage="router_failed", gateway_http_status=bad,  # type: ignore[arg-type]
        ) is None
    assert project_mobile_model_trace(
        traceparent=VALID_TRACE, mobile_request_id=VALID_REQUEST_ID,
        stage="router_failed", gateway_http_status=503,
    ) is not None


def test_same_trace_groups_multiple_distinct_stages_without_merging_attempt_span():
    first = project_mobile_model_trace(
        traceparent=VALID_TRACE, mobile_request_id=VALID_REQUEST_ID,
        stage="gateway_accepted",
    )
    next_span = "00-1234567890abcdef1234567890abcdef-ffffffffffffffff-01"
    second = project_mobile_model_trace(
        traceparent=next_span, mobile_request_id=VALID_REQUEST_ID,
        stage="sse_stream_opened",
    )
    assert first is not None and second is not None
    assert first.to_trace_record_kwargs()["correlation_id"] == second.to_trace_record_kwargs()["correlation_id"]
    assert first.parent_span_id != second.parent_span_id
