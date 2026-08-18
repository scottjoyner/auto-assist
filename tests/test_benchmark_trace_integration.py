import json

from assistx.benchmark_controller import publish_benchmark_outcome
from assistx.evaluation.trace_eval import evaluate_trace


def _task():
    return {
        "id": "bench-task-1",
        "kind": "adaptive_model_benchmark",
        "payload": {
            "benchmark": True,
            "benchmark_id": "b1",
            "task_family": "coding",
            "model": "qwen-test",
        },
    }


def test_benchmark_outcome_records_trace_without_router(monkeypatch, tmp_path):
    trace_path = tmp_path / "assistx-traces.jsonl"
    monkeypatch.setenv("ASSISTX_TRACE_JSONL_PATH", str(trace_path))
    monkeypatch.delenv("AUTO_ROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("AUTO_ROUTER_ADMIN_TOKEN", raising=False)

    result = publish_benchmark_outcome(
        _task(),
        "x1-370",
        "DONE",
        {"validation_passed": True, "tokens_per_second": 21.5},
    )

    assert result["published"] is False
    assert result["reason"] == "router_or_token_not_configured"
    assert result["trace_recorded"] is True
    assert result["trace_id"] == "benchmark-bench-task-1"

    trace = json.loads(trace_path.read_text().strip())
    assert trace["trace_id"] == "benchmark-bench-task-1"
    assert trace["spans"][1]["attributes"]["node_id"] == "x1-370"
    assert trace["spans"][2]["attributes"]["tokens_per_second"] == 21.5
    assert evaluate_trace(trace).passed is True


def test_failed_benchmark_trace_is_recorded_as_failure(monkeypatch, tmp_path):
    trace_path = tmp_path / "failed.jsonl"
    monkeypatch.setenv("ASSISTX_TRACE_JSONL_PATH", str(trace_path))
    monkeypatch.delenv("AUTO_ROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("AUTO_ROUTER_ADMIN_TOKEN", raising=False)

    publish_benchmark_outcome(
        _task(),
        "xwing",
        "FAILED",
        {"validation_passed": False},
    )

    trace = json.loads(trace_path.read_text().strip())
    assert trace["outcome"]["status"] == "failure"
    assert evaluate_trace(trace).passed is False
