from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from assistx.fleet_latency_shadow import (
    FleetLatencyMapV1,
    build_shadow_latency_plan,
)


NOW = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)


def _map() -> dict:
    return {
        "schema": "fleet-latency-map.v1",
        "captured_at": NOW.isoformat(),
        "origin_node_id": "x1-370",
        "stale_after_seconds": 300,
        "network_paths": [
            {
                "origin_node_id": "x1-370",
                "target_node_id": "deathstar",
                "transport": "tailscale",
                "rtt_ms_p50": 0.7,
                "rtt_ms_p95": 1.0,
                "jitter_ms_p95": 0.2,
                "loss_rate": 0,
                "observed_at": NOW.isoformat(),
                "samples": 8,
            },
            {
                "origin_node_id": "x1-370",
                "target_node_id": "xwing",
                "transport": "tailscale",
                "rtt_ms_p50": 12,
                "rtt_ms_p95": 15,
                "observed_at": NOW.isoformat(),
                "samples": 8,
            },
        ],
        "endpoints": [
            {
                "node_id": "deathstar",
                "model_id": "ling",
                "runtime_id": "llama.cpp@abc",
                "task_family": "decision_judge",
                "model_artifact_sha256": "a" * 64,
                "context_bucket": 4096,
                "concurrency": 1,
                "warm_state": "warm",
                "ttft_ms_p50": 90,
                "ttft_ms_p95": 130,
                "prompt_tok_s": 600,
                "decode_tok_s": 40,
                "wall_ms_p50": 500,
                "wall_ms_p95": 700,
                "observed_at": NOW.isoformat(),
                "samples": 20,
            },
            {
                "node_id": "xwing",
                "model_id": "k2",
                "runtime_id": "llama.cpp@def",
                "task_family": "decision_judge",
                "model_artifact_sha256": "b" * 64,
                "context_bucket": 4096,
                "concurrency": 1,
                "warm_state": "warm",
                "ttft_ms_p50": 60,
                "ttft_ms_p95": 90,
                "prompt_tok_s": 800,
                "decode_tok_s": 60,
                "wall_ms_p50": 350,
                "wall_ms_p95": 500,
                "observed_at": NOW.isoformat(),
                "samples": 20,
            },
        ],
        "pressures": [
            {
                "node_id": "deathstar",
                "inflight_tasks": 0,
                "max_concurrent": 2,
                "queue_depth": 0,
                "memory_pressure": 0.2,
                "thermal_pressure": 0.1,
                "runtime_warm": True,
                "captured_at": NOW.isoformat(),
            },
            {
                "node_id": "xwing",
                "inflight_tasks": 0,
                "max_concurrent": 1,
                "queue_depth": 0,
                "memory_pressure": 0.2,
                "thermal_pressure": 0.1,
                "runtime_warm": True,
                "captured_at": NOW.isoformat(),
            },
        ],
        "authority": {
            "routing_authority_changed": False,
            "admission_changed": False,
            "dispatch_allowed": False,
            "approval_granted": False,
            "claim_acquired": False,
            "mutation_allowed": False,
        },
    }


def _candidates() -> list[dict]:
    return [
        {
            "node_id": "deathstar",
            "model_id": "ling",
            "task_family": "decision_judge",
            "online": True,
            "quality_floor_passed": True,
            "quality_score": 0.9,
            "quality_confidence": 0.8,
            "allow_agent_runtime": True,
            "allow_code_execution": False,
        },
        {
            "node_id": "xwing",
            "model_id": "k2",
            "task_family": "decision_judge",
            "online": True,
            "quality_floor_passed": True,
            "quality_score": 0.8,
            "quality_confidence": 0.8,
            "allow_agent_runtime": True,
            "allow_code_execution": False,
        },
    ]


def test_schema_rejects_authority_widening() -> None:
    value = _map()
    value["authority"]["dispatch_allowed"] = True
    with pytest.raises(ValidationError):
        FleetLatencyMapV1.model_validate(value)


def test_schema_rejects_unknown_fields() -> None:
    value = _map()
    value["network_paths"][0]["admitted"] = True
    with pytest.raises(ValidationError):
        FleetLatencyMapV1.model_validate(value)


def test_shadow_plan_is_non_executable_and_authority_false() -> None:
    plan = build_shadow_latency_plan(
        latency_map=_map(),
        candidates=_candidates(),
        task_family="decision_judge",
        expected_prompt_tokens=128,
        expected_output_tokens=16,
        now=NOW,
    )
    assert plan["recommended"] is not None
    assert plan["executable"] is False
    assert set(plan["authority"].values()) == {False}


def test_faster_model_cannot_bypass_quality_floor() -> None:
    candidates = _candidates()
    candidates[1]["quality_floor_passed"] = False
    plan = build_shadow_latency_plan(
        latency_map=_map(),
        candidates=candidates,
        task_family="decision_judge",
        expected_prompt_tokens=128,
        expected_output_tokens=16,
        now=NOW,
    )
    assert plan["recommended"]["node_id"] == "deathstar"
    assert {
        (row["candidate"], row["reason"]) for row in plan["rejected"]
    } == {("xwing/k2", "quality_floor_failed")}


def test_stale_latency_evidence_is_not_used() -> None:
    value = _map()
    stale = (NOW - timedelta(minutes=10)).isoformat()
    for row in value["network_paths"]:
        if row["target_node_id"] == "xwing":
            row["observed_at"] = stale
    plan = build_shadow_latency_plan(
        latency_map=value,
        candidates=_candidates(),
        task_family="decision_judge",
        now=NOW,
    )
    assert plan["recommended"]["node_id"] == "deathstar"
    assert ("xwing/k2", "missing_fresh_latency_evidence") in {
        (row["candidate"], row["reason"]) for row in plan["rejected"]
    }


def test_draining_node_is_excluded_before_latency() -> None:
    candidates = _candidates()
    candidates[1]["status"] = "draining"
    plan = build_shadow_latency_plan(
        latency_map=_map(),
        candidates=candidates,
        task_family="decision_judge",
        now=NOW,
    )
    assert plan["recommended"]["node_id"] == "deathstar"
    assert ("xwing/k2", "operator_control") in {
        (row["candidate"], row["reason"]) for row in plan["rejected"]
    }


def test_fresh_dynamic_thermal_pressure_is_a_hard_stop() -> None:
    value = _map()
    value["pressures"][1]["thermal_pressure"] = 0.99
    plan = build_shadow_latency_plan(
        latency_map=value,
        candidates=_candidates(),
        task_family="decision_judge",
        now=NOW,
    )
    assert plan["recommended"]["node_id"] == "deathstar"
    assert ("xwing/k2", "thermal_hard_stop") in {
        (row["candidate"], row["reason"]) for row in plan["rejected"]
    }


def test_queue_delay_can_change_the_shadow_choice() -> None:
    value = _map()
    value["pressures"][1]["queue_depth"] = 8
    plan = build_shadow_latency_plan(
        latency_map=value,
        candidates=_candidates(),
        task_family="decision_judge",
        expected_prompt_tokens=128,
        expected_output_tokens=16,
        now=NOW,
    )
    assert plan["recommended"]["node_id"] == "deathstar"
    xwing = next(row for row in plan["alternatives"] if row["node_id"] == "xwing")
    assert xwing["queue_delay_ms"] > 0


def test_network_map_is_directional() -> None:
    value = _map()
    value["network_paths"][0]["origin_node_id"] = "deathstar"
    plan = build_shadow_latency_plan(
        latency_map=value,
        candidates=_candidates(),
        task_family="decision_judge",
        now=NOW,
    )
    assert plan["recommended"]["node_id"] == "xwing"
    assert ("deathstar/ling", "missing_fresh_latency_evidence") in {
        (row["candidate"], row["reason"]) for row in plan["rejected"]
    }


def test_agent_runtime_requirement_is_a_hard_gate() -> None:
    candidates = _candidates()
    candidates[1]["allow_agent_runtime"] = False
    plan = build_shadow_latency_plan(
        latency_map=_map(),
        candidates=candidates,
        task_family="decision_judge",
        requires_agent_runtime=True,
        now=NOW,
    )
    assert plan["recommended"]["node_id"] == "deathstar"
    assert ("xwing/k2", "agent_runtime_not_allowed") in {
        (row["candidate"], row["reason"]) for row in plan["rejected"]
    }


def test_end_to_end_endpoint_timing_does_not_double_count_network() -> None:
    value = _map()
    endpoint = value["endpoints"][1]
    endpoint["measurement_scope"] = "end_to_end"
    endpoint["measurement_origin_node_id"] = "x1-370"

    plan = build_shadow_latency_plan(
        latency_map=value,
        candidates=[_candidates()[1]],
        task_family="decision_judge",
        expected_prompt_tokens=128,
        expected_output_tokens=16,
        now=NOW,
    )

    assert plan["recommended"]["measurement_scope"] == "end_to_end"
    assert plan["recommended"]["network_ms"] == 0.0


def test_end_to_end_endpoint_from_wrong_origin_is_not_reused() -> None:
    value = _map()
    endpoint = value["endpoints"][1]
    endpoint["measurement_scope"] = "end_to_end"
    endpoint["measurement_origin_node_id"] = "deathstar"

    plan = build_shadow_latency_plan(
        latency_map=value,
        candidates=[_candidates()[1]],
        task_family="decision_judge",
        now=NOW,
    )

    assert plan["recommended"] is None
    assert plan["rejected"] == [
        {"candidate": "xwing/k2", "reason": "missing_fresh_latency_evidence"}
    ]


def test_candidate_runtime_identity_filters_same_model_observations() -> None:
    candidates = [_candidates()[1]]
    candidates[0]["runtime_id"] = "llama.cpp@different"

    plan = build_shadow_latency_plan(
        latency_map=_map(),
        candidates=candidates,
        task_family="decision_judge",
        now=NOW,
    )

    assert plan["recommended"] is None
    assert plan["rejected"] == [
        {"candidate": "xwing/k2", "reason": "missing_fresh_latency_evidence"}
    ]



def test_reasoning_aware_ttft_requires_semantic_timing_evidence() -> None:
    value = _map()
    endpoint = value["endpoints"][0]
    endpoint["ttft_basis"] = "reasoning_or_content"
    with pytest.raises(ValidationError, match="reasoning or content timing"):
        FleetLatencyMapV1.model_validate(value)


def test_reasoning_aware_ttft_accepts_first_reasoning_before_content() -> None:
    value = _map()
    endpoint = value["endpoints"][0]
    endpoint.update(
        {
            "ttft_basis": "reasoning_or_content",
            "ttft_ms_p50": 90,
            "ttft_ms_p95": 130,
            "first_sse_event_ms_p50": 70,
            "first_sse_event_ms_p95": 100,
            "first_reasoning_ms_p50": 90,
            "first_reasoning_ms_p95": 130,
            "first_content_ms_p50": 300,
            "first_content_ms_p95": 450,
        }
    )

    document = FleetLatencyMapV1.model_validate(value)
    row = document.endpoints[0]
    assert row.ttft_basis == "reasoning_or_content"
    assert row.first_reasoning_ms_p50 == 90
    assert row.first_content_ms_p50 == 300


def test_optional_latency_percentiles_must_be_paired_and_ordered() -> None:
    value = _map()
    value["endpoints"][0]["first_content_ms_p50"] = 300
    with pytest.raises(ValidationError, match="p50/p95"):
        FleetLatencyMapV1.model_validate(value)

    value = _map()
    value["endpoints"][0]["first_content_ms_p50"] = 300
    value["endpoints"][0]["first_content_ms_p95"] = 200
    with pytest.raises(ValidationError, match="must be >="):
        FleetLatencyMapV1.model_validate(value)


def test_shadow_plan_surfaces_reasoning_and_visible_latency_separately() -> None:
    value = _map()
    endpoint = value["endpoints"][0]
    endpoint.update(
        {
            "ttft_basis": "reasoning_or_content",
            "first_reasoning_ms_p50": 90,
            "first_reasoning_ms_p95": 130,
            "first_content_ms_p50": 300,
            "first_content_ms_p95": 450,
        }
    )

    plan = build_shadow_latency_plan(
        latency_map=value,
        candidates=[_candidates()[0]],
        task_family="decision_judge",
        now=NOW,
    )

    assert plan["recommended"]["ttft_basis"] == "reasoning_or_content"
    assert plan["recommended"]["first_reasoning_ms_p50"] == 90
    assert plan["recommended"]["first_content_ms_p50"] == 300
