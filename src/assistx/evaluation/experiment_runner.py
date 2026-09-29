"""Offline runner that turns JSONL trace corpora into promotion artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .experiment_artifact import write_experiment_artifact
from .experiment_manifest import ExperimentManifest
from .experiment_results import compare_variants
from .promotion import PromotionThresholds, evaluate_promotion


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"line {line_number} must contain a JSON object")
        traces.append(payload)
    return traces


def run_experiment(
    *,
    manifest: ExperimentManifest,
    baseline_path: str | Path,
    candidate_path: str | Path,
    output_path: str | Path,
    thresholds: PromotionThresholds,
    safety_regressions: int = 0,
    fault_gates_passed: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    baseline = load_jsonl(baseline_path)
    candidate = load_jsonl(candidate_path)
    comparison = compare_variants("baseline", baseline, "candidate", candidate)
    promotion = evaluate_promotion(
        comparison,
        thresholds=thresholds,
        safety_regressions=safety_regressions,
        fault_gates_passed=fault_gates_passed,
    )
    write_experiment_artifact(
        output_path,
        manifest=manifest,
        comparison=comparison,
        promotion=promotion,
        metadata=metadata,
    )
    return {
        "manifest_id": manifest.manifest_id,
        "baseline_runs": comparison["baseline"].runs,
        "candidate_runs": comparison["candidate"].runs,
        "eligible": promotion.eligible,
        "reasons": list(promotion.reasons),
        "output_path": str(output_path),
    }
