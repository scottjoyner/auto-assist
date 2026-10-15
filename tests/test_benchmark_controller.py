from types import SimpleNamespace

import pytest

from assistx import benchmark_controller, fleet_node_agent
from assistx.benchmark_controller import BenchmarkController, publish_benchmark_outcome


class FakeNeo:
    def __init__(self):
        self.tasks = []

    def create_tasks_batch(self, tasks):
        self.tasks.extend(tasks)
        return len(tasks)


def request(**overrides):
    value = {
        "benchmark_id": "x1:qwen:coding",
        "node_id": "x1",
        "model_id": "qwen",
        "task_family": "coding",
        "requires_model_load": False,
        "execution_mode": "dry_run",
    }
    value.update(overrides)
    return value


def test_controller_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ASSISTX_BENCHMARK_CONTROLLER_ENABLED", raising=False)
    controller = BenchmarkController()
    neo = FakeNeo()

    result = controller.tick(neo, lambda: {"requests": [request()]}, force=True)

    assert result["blocked"] is True
    assert result["reason"] == "controller_disabled"
    assert neo.tasks == []


def test_controller_creates_targeted_loaded_only_task(monkeypatch):
    monkeypatch.setenv("ASSISTX_BENCHMARK_CONTROLLER_ENABLED", "true")
    controller = BenchmarkController()
    neo = FakeNeo()

    result = controller.tick(neo, lambda: {"requests": [
        request(),
        request(benchmark_id="bad", requires_model_load=True),
    ]})

    assert result["created"] == 1
    task = neo.tasks[0]
    assert task["target_agent_id"] == "x1"
    assert task["payload"]["allow_model_load"] is False
    assert task["payload"]["cases"]
    assert "command" not in task["payload"]


def test_node_agent_scores_deterministic_benchmark(monkeypatch, tmp_path):
    def fake_http(method, url, **kwargs):
        return 200, {
            "choices": [{"message": {"content": "sum values duplicate rollback"}}],
            "usage": {"completion_tokens": 8},
        }

    monkeypatch.setattr(fleet_node_agent, "_http", fake_http)
    outcome = fleet_node_agent.execute_task(
        {
            "id": "bench",
            "required_capabilities": ["llm"],
            "payload": {
                "benchmark": True,
                "model": "qwen",
                "task_family": "coding",
                "max_tokens_per_case": 64,
                "cases": [
                    {"prompt": "test", "required_terms": ["sum", "values"], "min_chars": 5},
                ],
            },
        },
        "http://localhost:1234",
        str(tmp_path),
    )

    assert outcome["status"] == "DONE"
    assert outcome["result"]["quality_score"] == 1.0
    assert outcome["result"]["validation_passed"] is True


def test_outcome_publisher_is_metadata_only(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_BASE_URL", "http://router")
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-only")
    captured = {}

    class FakeSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, url, json, headers, timeout, allow_redirects):
            captured.update(json)
            captured["redirects_allowed"] = allow_redirects
            captured["trust_env"] = self.trust_env
            captured["admin_header_present"] = bool(headers.get("X-Admin-Token"))
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(benchmark_controller.requests, "Session", FakeSession)
    monkeypatch.setattr(
        benchmark_controller.requests, "post",
        lambda *_args, **_kwargs: pytest.fail("direct requests.post bypasses the safe session"),
    )
    result = publish_benchmark_outcome(
        {
            "id": "task",
            "kind": "adaptive_model_benchmark",
            "payload_json": '{"benchmark":true,"model":"qwen","task_family":"coding"}',
        },
        "x1", "DONE",
        {"quality_score": 0.8, "validation_passed": True, "tokens_per_second": 22},
    )

    assert result["published"] is True
    assert captured["metadata"]["quality_score"] == 0.8
    assert captured["redirects_allowed"] is False
    assert captured["trust_env"] is False
    assert captured["admin_header_present"] is True
    assert "prompt" not in str(captured).lower()


def test_outcome_publisher_redirect_response_is_not_published(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_BASE_URL", "http://router.invalid:8088")
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-only")
    options = {}

    class FakeSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, url, *, json, headers, timeout, allow_redirects):
            options["redirects_allowed"] = allow_redirects
            options["trust_env"] = self.trust_env
            options["has_admin_header"] = bool(headers.get("X-Admin-Token"))
            return SimpleNamespace(status_code=302)

    monkeypatch.setattr(benchmark_controller.requests, "Session", FakeSession)
    monkeypatch.setattr(
        benchmark_controller.requests, "post",
        lambda *_a, **_k: pytest.fail("legacy redirect-following session must not run"),
    )
    result = publish_benchmark_outcome(
        {"id": "task", "kind": "adaptive_model_benchmark", "payload": {"benchmark": True}},
        "synthetic-node", "DONE", {"validation_passed": True},
    )
    assert result == {"published": False, "status_code": 302}
    assert options == {
        "redirects_allowed": False,
        "trust_env": False,
        "has_admin_header": True,
    }

def test_outcome_transport_exception_never_echoes_credentials(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_BASE_URL", "http://user:synthetic-password@router.invalid:8088")
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "SYNTHETIC_SECRET_NEVER_PRINT")

    class FakeSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, *_args, **_kwargs):
            raise OSError("SYNTHETIC_SECRET_NEVER_PRINT untrusted failed origin URL")

    monkeypatch.setattr(benchmark_controller.requests, "Session", FakeSession)
    result = publish_benchmark_outcome(
        {"id": "synthetic", "kind": "adaptive_model_benchmark", "payload": {"benchmark": True}},
        "synthetic-node", "DONE", {"validation_passed": False},
    )
    assert result == {"published": False, "reason": "router_outcome_transport_unverified"}
    assert "SYNTHETIC_SECRET_NEVER_PRINT" not in str(result)
    assert "synthetic-password" not in str(result)
