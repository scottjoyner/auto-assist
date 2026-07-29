from assistx.benchmark_controller import BenchmarkController, publish_benchmark_outcome
from assistx import fleet_node_agent


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
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "token")
    captured = {}

    class Response:
        status_code = 200

    def fake_post(url, json, headers, timeout):
        captured.update(json)
        return Response()

    monkeypatch.setattr("assistx.benchmark_controller.requests.post", fake_post)
    result = publish_benchmark_outcome(
        {
            "id": "task",
            "kind": "adaptive_model_benchmark",
            "payload_json": '{"benchmark":true,"model":"qwen","task_family":"coding"}',
        },
        "x1",
        "DONE",
        {"quality_score": 0.8, "validation_passed": True, "tokens_per_second": 22},
    )

    assert result["published"] is True
    assert captured["metadata"]["quality_score"] == 0.8
    assert "prompt" not in str(captured).lower()
