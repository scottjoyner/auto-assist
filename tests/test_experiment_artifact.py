import json

from assistx.evaluation.experiment_artifact import write_experiment_artifact
from assistx.evaluation.experiment_manifest import ExperimentManifest
from assistx.evaluation.experiment_results import compare_variants
from assistx.evaluation.promotion import PromotionThresholds, evaluate_promotion
from assistx.evaluation.trace_recorder import benchmark_trace


def _trace(task_id: str, *, duration_ms: float):
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


def test_artifact_round_trips_manifest_comparison_and_promotion(tmp_path):
    manifest = ExperimentManifest(
        name="cache-affinity",
        variant="shadow",
        baseline_id="router-current",
        source_repository="scottjoyner/auto-router",
        source_commit="abc123",
        corpus_id="repeated-prefix-v1",
    )
    comparison = compare_variants(
        "baseline",
        [_trace(f"b{i}", duration_ms=200) for i in range(5)],
        "candidate",
        [_trace(f"c{i}", duration_ms=150) for i in range(5)],
    )
    promotion = evaluate_promotion(
        comparison,
        thresholds=PromotionThresholds(min_runs=5, max_mean_duration_ms_delta=0),
        fault_gates_passed=True,
    )
    target = tmp_path / "experiment.json"
    write_experiment_artifact(
        target,
        manifest=manifest,
        comparison=comparison,
        promotion=promotion,
        metadata={"operator_state": "review_required"},
    )
    payload = json.loads(target.read_text())
    assert payload["schema_version"] == "assistx.experiment-artifact.v1"
    assert payload["manifest"]["name"] == "cache-affinity"
    assert payload["promotion"]["eligible"] is True
    assert payload["metadata"]["operator_state"] == "review_required"
