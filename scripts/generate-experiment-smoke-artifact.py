#!/usr/bin/env python3
"""Generate a deterministic experiment artifact for CI plumbing validation."""

from pathlib import Path

from assistx.evaluation.experiment_manifest import ExperimentManifest
from assistx.evaluation.experiment_results import compare_variants
from assistx.evaluation.experiment_artifact import write_experiment_artifact
from assistx.evaluation.promotion import PromotionThresholds, evaluate_promotion
from assistx.evaluation.trace_recorder import benchmark_trace


def trace(task_id: str, duration_ms: float):
    payload = benchmark_trace(
        task_id=task_id,
        node_id="ci-fixture",
        model="fixture-model",
        task_family="coding",
        success=True,
        validation_passed=True,
        experiment={
            "name": "ci-artifact-smoke",
            "variant": "fixture",
            "baseline_id": "fixture-baseline",
            "source_commit": "ci",
        },
    )
    payload["outcome"]["duration_ms"] = duration_ms
    return payload


def main() -> int:
    baseline = [trace(f"b{i}", 200.0) for i in range(5)]
    candidate = [trace(f"c{i}", 150.0) for i in range(5)]
    comparison = compare_variants("baseline", baseline, "candidate", candidate)
    promotion = evaluate_promotion(
        comparison,
        thresholds=PromotionThresholds(min_runs=5, max_mean_duration_ms_delta=0),
        fault_gates_passed=True,
    )
    manifest = ExperimentManifest(
        name="ci-artifact-smoke",
        variant="fixture-candidate",
        baseline_id="fixture-baseline",
        source_repository="scottjoyner/auto-assist",
        source_commit="ci",
        corpus_id="deterministic-fixture-v1",
    )
    output = Path("artifacts/experiments/ci-smoke.json")
    write_experiment_artifact(
        output,
        manifest=manifest,
        comparison=comparison,
        promotion=promotion,
        metadata={"purpose": "validate experiment artifact plumbing"},
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
