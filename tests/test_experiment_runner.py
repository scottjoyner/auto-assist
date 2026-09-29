import json

from assistx.evaluation.experiment_manifest import ExperimentManifest
from assistx.evaluation.experiment_runner import run_experiment
from assistx.evaluation.promotion import PromotionThresholds
from assistx.evaluation.trace_recorder import benchmark_trace


def _write_jsonl(path, traces):
    path.write_text("\n".join(json.dumps(trace) for trace in traces) + "\n")


def _trace(task_id: str, duration_ms: float):
    trace = benchmark_trace(
        task_id=task_id,
        node_id="x1-370",
        model="qwen-test",
        task_family="coding",
        success=True,
        validation_passed=True,
    )
    trace["outcome"]["duration_ms"] = duration_ms
    return trace


def test_runner_writes_machine_readable_promotion_artifact(tmp_path):
    baseline_path = tmp_path / "baseline.jsonl"
    candidate_path = tmp_path / "candidate.jsonl"
    output_path = tmp_path / "artifact.json"
    _write_jsonl(baseline_path, [_trace(f"b{i}", 200) for i in range(5)])
    _write_jsonl(candidate_path, [_trace(f"c{i}", 150) for i in range(5)])
    manifest = ExperimentManifest(
        name="context-compression",
        variant="candidate",
        baseline_id="raw-v1",
        source_repository="scottjoyner/auto-assist",
        source_commit="abc123",
        corpus_id="corpus-v1",
    )
    result = run_experiment(
        manifest=manifest,
        baseline_path=baseline_path,
        candidate_path=candidate_path,
        output_path=output_path,
        thresholds=PromotionThresholds(min_runs=5, max_mean_duration_ms_delta=0),
        fault_gates_passed=True,
        metadata={"operator_state": "review_required"},
    )
    assert result["eligible"] is True
    payload = json.loads(output_path.read_text())
    assert payload["promotion"]["eligible"] is True
    assert payload["comparison"]["median_duration_ms_delta"] == -50
    assert payload["comparison"]["p95_duration_ms_delta"] == -50
