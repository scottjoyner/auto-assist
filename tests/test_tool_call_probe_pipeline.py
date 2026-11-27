from __future__ import annotations

import copy

from assistx import benchmark_controller, fleet_node_agent, runtime_projection_v2
from assistx.tool_call_reliability import (
    REQUIRED_TOOL_CALL_PROBES,
    build_tool_use_probe_evidence,
    model_identity_fingerprint,
    tool_call_probe_cases,
)


def _passing_evidence() -> dict:
    observations = []
    for probe in tool_call_probe_cases():
        probe_id = probe["probe_id"]
        if probe_id == "hidden_info_spelunking":
            calls = []
        elif probe_id in {"duplicate_semantic_call", "malformed_arguments"}:
            calls = [{"name": "node_health", "arguments": '{"node_id":"xwing"}'}]
        else:
            calls = [{"name": "fleet_status", "arguments": "{}"}]
        observations.append(
            {
                "probe_id": probe_id,
                "tool_calls": calls,
                "raw_output": "",
                "expected_tools": probe["expected_tools"],
                "forbidden_tools": probe["forbidden_tools"],
                "expects_no_tool": probe["expects_no_tool"],
            }
        )
    return build_tool_use_probe_evidence(observations)


def _identity() -> dict:
    value = {
        "node_id": "x1-370",
        "runtime_instance_id": "x1-lmstudio-1234",
        "runtime_version": "lm-studio/0.4.24+1",
        "model_instance_id": "model-x1-ornith-quality",
        "artifact_fingerprint": "sha256:ornith",
        "quantization": "Q4_K_M",
        "context_length": 262144,
    }
    value["fingerprint"] = model_identity_fingerprint(value)
    return value


def test_five_read_only_probe_canaries_pass_without_executing_tools(monkeypatch, tmp_path):
    calls = []

    def fake_http(method, url, **kwargs):
        calls.append((method, url, kwargs.get("data") or {}))
        payload = kwargs.get("data") or {}
        if payload.get("tools"):
            prompt = str(payload["messages"][0]["content"])
            if "Do not inspect files" in prompt:
                message = {"content": "UNAVAILABLE", "tool_calls": []}
            elif "node xwing" in prompt:
                message = {
                    "content": "",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "node_health",
                                "arguments": '{"node_id":"xwing"}',
                            },
                        }
                    ],
                }
            else:
                message = {
                    "content": "",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "fleet_status",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            return 200, {"choices": [{"message": message}]}
        return 200, {
            "choices": [
                {
                    "message": {
                        "content": (
                            "fleet_status xwing approval read-only benchmark "
                            "response with enough content"
                        )
                    }
                }
            ],
            "usage": {"completion_tokens": 12},
        }

    monkeypatch.setattr(fleet_node_agent, "_http", fake_http)
    outcome = fleet_node_agent.execute_task(
        {
            "id": "tool-probe",
            "required_capabilities": ["llm"],
            "payload": {
                "benchmark": True,
                "model": "ornith",
                "task_family": "tool_use",
                "max_tokens_per_case": 64,
                "deadline_seconds": 60,
                "cases": [
                    {
                        "prompt": "standard benchmark",
                        "required_terms": ["fleet_status"],
                        "min_chars": 10,
                    }
                ],
                "tool_call_probes": tool_call_probe_cases(),
            },
        },
        "http://localhost:1234",
        str(tmp_path),
    )

    assert outcome["status"] == "DONE"
    evidence = outcome["result"]["tool_call_probe"]
    assert evidence["passed"] is True
    assert set(evidence["passed_probes"]) == set(REQUIRED_TOOL_CALL_PROBES)
    tool_requests = [payload for _, _, payload in calls if payload.get("tools")]
    assert len(tool_requests) == 5
    assert all(url.endswith("/v1/chat/completions") for _, url, _ in calls)
    assert all("tool_choice" in payload for payload in tool_requests)


def test_controller_binds_tool_probe_task_to_exact_model_identity(monkeypatch):
    identity = _identity()
    monkeypatch.setattr(
        benchmark_controller,
        "_resolve_model_identity",
        lambda _neo, _node, _model: dict(identity),
    )
    task = benchmark_controller.BenchmarkController()._task(
        {
            "benchmark_id": "x1:ornith:tool_use",
            "node_id": "x1-370",
            "model_id": "ornith",
            "task_family": "tool_use",
            "requires_model_load": False,
            "execution_mode": "dry_run",
        },
        neo=object(),
    )

    assert task is not None
    assert task["payload"]["model_identity_fingerprint"] == identity["fingerprint"]
    assert len(task["payload"]["tool_call_probes"]) == 5


class _WriteResult:
    def consume(self):
        return None


class _WriteSession:
    def __init__(self, captured):
        self.captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def run(self, query, params=None):
        self.captured["query"] = query
        self.captured["params"] = dict(params or {})
        return _WriteResult()


class _WriteNeo:
    def __init__(self):
        self.captured = {}

    def _session(self):
        return _WriteSession(self.captured)


def test_probe_evidence_is_persisted_for_next_generation_only(monkeypatch):
    identity = _identity()
    monkeypatch.setattr(
        benchmark_controller,
        "_resolve_model_identity",
        lambda _neo, _node, _model: dict(identity),
    )
    monkeypatch.setattr(
        benchmark_controller,
        "_projection_generation",
        lambda _neo: 635,
    )
    neo = _WriteNeo()
    task = {
        "id": "task-1",
        "kind": "adaptive_model_benchmark",
        "payload": {
            "benchmark": True,
            "benchmark_id": "x1:ornith:tool_use",
            "task_family": "tool_use",
            "target_node": "x1-370",
            "model": "ornith",
            "model_identity": identity,
            "model_identity_fingerprint": identity["fingerprint"],
        },
    }
    result = {
        "status": "DONE",
        "result": {
            "quality_score": 0.95,
            "validation_passed": True,
            "tool_call_probe": _passing_evidence(),
        },
    }

    recorded = benchmark_controller.record_tool_call_probe_evidence(
        neo, task, "x1-370", "DONE", result
    )

    assert recorded["recorded"] is True
    assert recorded["not_before_generation"] == 636
    assert recorded["quality_floor_passed"] is True
    assert neo.captured["params"]["identity_fingerprint"] == identity["fingerprint"]
    assert neo.captured["params"]["not_before_generation"] == 636


def _projection_document(generation: int, identity: dict) -> dict:
    return {
        "generation": generation,
        "generated_at_ms": 1_000,
        "expires_at_ms": 10_000,
        "providers": [
            {
                "node_id": identity["node_id"],
                "runtime_instance_id": identity["runtime_instance_id"],
                "runtime_version": identity["runtime_version"],
                "models": [
                    {
                        "alias": "ornith",
                        "provider_model": "ornith",
                        "model_instance_id": identity["model_instance_id"],
                        "artifact_fingerprint": identity["artifact_fingerprint"],
                        "quantization": identity["quantization"],
                        "context_window": identity["context_length"],
                    }
                ],
            }
        ],
    }


def test_projection_hides_pending_probe_until_next_generation(monkeypatch):
    identity = _identity()
    hint = {
        "routing_roles": ["full_agent"],
        "worker_mode": "agent",
        "allow_agent_runtime": True,
        "allow_code_execution": True,
        "task_family_scores": {},
        "tool_call_probe_evidence": {
            "quality_score": 0.95,
            "quality_floor_passed": True,
            "tool_call_probe": _passing_evidence(),
            "model_identity_fingerprint": identity["fingerprint"],
            "not_before_generation": 636,
            "expires_at_ts": 9_000,
            "observed_at_ts": 900,
        },
    }
    monkeypatch.setattr(
        runtime_projection_v2,
        "benchmark_projection_index",
        lambda _factory: {("x1-370", "ornith"): copy.deepcopy(hint)},
    )
    monkeypatch.setattr(
        runtime_projection_v2,
        "node_routing_policy_index",
        lambda _factory: {
            "x1-370": {
                "routing_roles": ["full_agent"],
                "worker_mode": "agent",
                "allow_agent_runtime": True,
                "allow_code_execution": True,
            }
        },
    )

    pending = _projection_document(635, identity)
    runtime_projection_v2._apply_benchmark_routing(pending, lambda: None)
    assert pending["providers"][0]["models"][0]["task_family_scores"] == {}

    eligible = _projection_document(636, identity)
    runtime_projection_v2._apply_benchmark_routing(eligible, lambda: None)
    score = eligible["providers"][0]["models"][0]["task_family_scores"]["tool_use"]
    assert score["quality_floor_passed"] is True
    assert score["tool_call_probe"]["passed"] is True
    assert score["model_identity_fingerprint"] == identity["fingerprint"]
    assert eligible["expires_at_ms"] == 9_000

    changed = _identity()
    changed["artifact_fingerprint"] = "sha256:different"
    changed["fingerprint"] = model_identity_fingerprint(changed)
    mismatch = _projection_document(636, changed)
    runtime_projection_v2._apply_benchmark_routing(mismatch, lambda: None)
    assert mismatch["providers"][0]["models"][0]["task_family_scores"] == {}


def test_publisher_unwraps_node_agent_benchmark_envelope(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_BASE_URL", "http://router")
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "token")
    captured = {}

    class Response:
        status_code = 200

    def fake_post(url, json, headers, timeout):
        captured.update(json)
        return Response()

    monkeypatch.setattr(benchmark_controller.requests, "post", fake_post)
    result = benchmark_controller.publish_benchmark_outcome(
        {
            "id": "task",
            "kind": "adaptive_model_benchmark",
            "payload": {
                "benchmark": True,
                "model": "ornith",
                "task_family": "tool_use",
            },
        },
        "x1-370",
        "DONE",
        {
            "status": "DONE",
            "summary": "benchmark quality=0.95",
            "result": {
                "quality_score": 0.95,
                "validation_passed": True,
                "tokens_per_second": 22.0,
                "case_count": 3,
                "tool_call_probe": _passing_evidence(),
            },
        },
    )

    assert result["published"] is True
    assert captured["tokens_per_second"] == 22.0
    assert captured["validation_passed"] is True
    assert captured["metadata"]["quality_score"] == 0.95
    assert captured["metadata"]["tool_call_probe"]["passed"] is True


def test_transport_failure_fails_all_probe_canaries(monkeypatch):
    def offline_http(_method, _url, **_kwargs):
        return 0, {"detail": "connection refused"}

    monkeypatch.setattr(fleet_node_agent, "_http", offline_http)
    evidence = fleet_node_agent._run_tool_call_probes(
        {
            "model": "ornith",
            "max_tokens_per_case": 32,
            "deadline_seconds": 5,
            "tool_call_probes": tool_call_probe_cases(),
        },
        "http://127.0.0.1:1234",
    )

    assert evidence["passed"] is False
    assert set(evidence["failed_probes"]) == set(REQUIRED_TOOL_CALL_PROBES)
    assert all(
        "probe_transport_failed" in row["failures"]
        for row in evidence["results"]
    )
