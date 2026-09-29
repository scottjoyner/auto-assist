import json

from assistx.evaluation.trace_eval import evaluate_trace
from assistx.evaluation.trace_recorder import append_trace_jsonl, benchmark_trace


def test_benchmark_trace_is_eval_compatible():
    trace = benchmark_trace(
        task_id="task-1",
        node_id="x1-370",
        model="qwen-test",
        task_family="coding",
        success=True,
        validation_passed=True,
        tokens_per_second=20.0,
    )
    result = evaluate_trace(trace)
    assert result.passed is True
    assert trace["spans"][1]["attributes"]["network_path"] == "local_or_tailnet"


def test_jsonl_sink_is_opt_in_and_redacts(tmp_path):
    trace = benchmark_trace(
        task_id="task-2",
        node_id="xwing",
        model="qwen-test",
        task_family="reasoning",
        success=True,
        validation_passed=True,
    )
    trace["spans"][0]["attributes"]["api_key"] = "secret-value"
    assert append_trace_jsonl(trace, "") is None

    target = tmp_path / "traces" / "assistx.jsonl"
    assert append_trace_jsonl(trace, target) == str(target)
    payload = json.loads(target.read_text().strip())
    assert payload["spans"][0]["attributes"]["api_key"] == "[REDACTED]"
