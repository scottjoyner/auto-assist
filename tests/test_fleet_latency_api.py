from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from assistx.fleet_latency_api import (
    build_fleet_latency_router,
    latency_map_projection,
)
from assistx.fleet_latency_shadow import FleetLatencyMapV1


NOW = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)


def _document() -> FleetLatencyMapV1:
    return FleetLatencyMapV1.model_validate(
        {
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
                    "jitter_ms_p95": 0.1,
                    "loss_rate": 0,
                    "observed_at": NOW.isoformat(),
                    "samples": 4,
                }
            ],
            "endpoints": [
                {
                    "node_id": "deathstar",
                    "model_id": "ling",
                    "runtime_id": "llama.cpp@abc",
                    "task_family": "decision_judge",
                    "model_artifact_sha256": "a" * 64,
                    "measurement_scope": "runtime_local",
                    "ttft_ms_p50": 90,
                    "ttft_ms_p95": 120,
                    "prompt_tok_s": 600,
                    "decode_tok_s": 40,
                    "wall_ms_p50": 500,
                    "wall_ms_p95": 650,
                    "observed_at": NOW.isoformat(),
                    "samples": 20,
                }
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
                }
            ],
        }
    )


def _app(loader, auth=lambda: "operator") -> FastAPI:
    app = FastAPI()
    app.include_router(build_fleet_latency_router(auth, loader=loader))
    return app


def test_projection_reports_document_freshness_without_authority() -> None:
    result = latency_map_projection(_document(), now=NOW)
    assert result["freshness"]["age_seconds"] == 0
    assert result["freshness"]["document_stale"] is False
    assert set(result["authority"].values()) == {False}


def test_get_projection_is_auth_protected() -> None:
    def denied():
        raise HTTPException(status_code=401, detail="Unauthorized")

    client = TestClient(_app(lambda: _document(), auth=denied))
    response = client.get("/api/fleet/latency-map")
    assert response.status_code == 401


def test_missing_artifact_is_service_unavailable_not_empty_truth() -> None:
    def missing():
        raise FileNotFoundError("missing")

    client = TestClient(_app(missing))
    response = client.get("/api/fleet/latency-map")
    assert response.status_code == 503
    assert response.json()["detail"] == "Fleet latency evidence unavailable"


def test_invalid_artifact_is_service_unavailable_not_silently_ignored() -> None:
    def invalid():
        raise ValueError("invalid")

    client = TestClient(_app(invalid))
    response = client.get("/api/fleet/latency-map")
    assert response.status_code == 503
    assert response.json()["detail"] == "Fleet latency evidence invalid"


def test_shadow_plan_endpoint_is_computation_only() -> None:
    client = TestClient(_app(lambda: _document()))
    response = client.post(
        "/api/fleet/latency-map/shadow-plan",
        json={
            "task_family": "decision_judge",
            "expected_prompt_tokens": 128,
            "expected_output_tokens": 16,
            "candidates": [
                {
                    "node_id": "deathstar",
                    "model_id": "ling",
                    "runtime_id": "llama.cpp@abc",
                    "model_artifact_sha256": "a" * 64,
                    "task_family": "decision_judge",
                    "online": True,
                    "quality_floor_passed": True,
                    "quality_score": 0.9,
                    "quality_confidence": 0.9,
                    "allow_agent_runtime": True,
                    "allow_code_execution": False,
                }
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["recommended"]["node_id"] == "deathstar"
    assert body["executable"] is False
    assert set(body["authority"].values()) == {False}


def test_shadow_plan_request_rejects_unknown_fields() -> None:
    client = TestClient(_app(lambda: _document()))
    response = client.post(
        "/api/fleet/latency-map/shadow-plan",
        json={
            "task_family": "decision_judge",
            "candidates": [{"node_id": "deathstar"}],
            "dispatch": True,
        },
    )
    assert response.status_code == 422
