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
    assert params["correlation_id"] == "mobile-model:" + VALID_REQUEST_ID
    assert params["payload"]["trace_id"] == "1234567890abcdef1234567890abcdef"
    assert params["correlation_id"] != params["payload"]["trace_id"]
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


def test_existing_tracegroup_writer_accepts_projected_receipt_on_disposable_fake():
    """No real Neo4j connection: exercise actual graph writer against fake Cypher store."""
    import json
    from assistx.contracts.tests.fake_neo4j import FakeNeo4j
    from assistx.swarm_core import record_trace_event

    fake = FakeNeo4j(apoc=False)
    projection = project_mobile_model_trace(
        traceparent=VALID_TRACE, mobile_request_id=VALID_REQUEST_ID,
        stage="router_http_accepted", gateway_http_status=200,
    )
    assert projection is not None
    event_id = record_trace_event(fake, **projection.to_trace_record_kwargs())
    assert event_id.startswith("trace:")
    assert fake.has_node("TraceEvent", "event_id", event_id)
    assert fake.has_node("TraceGroup", "correlation_id", "mobile-model:" + VALID_REQUEST_ID)
    assert not fake.has_relationship("FOR_TASK")
    assert not fake.has_relationship("FOR_ROUTE")
    event_query = next(
        row for row in fake.runs
        if "TraceEvent" in row["cypher"] and "payload_json" in row["params"]
    )
    persisted_payload = json.loads(event_query["params"]["payload_json"])
    assert persisted_payload["mobile_request_id"] == VALID_REQUEST_ID
    assert persisted_payload["gateway_http_status"] == 200
    assert persisted_payload["attestation_level"] == "observational-only"
    assert all(
        forbidden not in str(event_query["params"]).lower()
        for forbidden in ("authorization", "tailscale", "prompt", "password")
    )


def test_existing_tracegroup_groups_multiple_model_stages_without_creating_authority_links():
    from assistx.contracts.tests.fake_neo4j import FakeNeo4j
    from assistx.swarm_core import record_trace_event

    fake = FakeNeo4j(apoc=False)
    for stage in ("gateway_accepted", "router_http_accepted", "sse_stream_opened"):
        projection = project_mobile_model_trace(
            traceparent=VALID_TRACE,
            mobile_request_id=VALID_REQUEST_ID,
            stage=stage,
        )
        assert projection is not None
        record_trace_event(fake, **projection.to_trace_record_kwargs())
    groups = [row for row in fake.runs if "MERGE (g:TraceGroup" in row["cypher"]]
    assert len(groups) == 3
    assert all(row["params"]["correlation_id"] == "mobile-model:" + VALID_REQUEST_ID for row in groups)
    assert not fake.has_relationship("FOR_TASK")
    assert not fake.has_relationship("FOR_ASSIGNMENT")
    assert not fake.has_relationship("FOR_ROUTE")


def test_client_selected_trace_id_cannot_reuse_graph_task_correlation_id():
    """An attacker can choose traceparent but cannot bind a task TraceGroup."""
    client_chosen = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-0123456789abcdef-01"
    obs = project_mobile_model_trace(
        traceparent=client_chosen,
        mobile_request_id=VALID_REQUEST_ID,
        stage="gateway_accepted",
    )
    assert obs is not None
    row = obs.to_trace_record_kwargs()
    assert row["payload"]["trace_id"] == "a" * 32
    assert row["correlation_id"] != "a" * 32
    assert row["correlation_id"] == "mobile-model:" + VALID_REQUEST_ID
    assert row["event_type"] == "mobile.model.gateway_accepted"
