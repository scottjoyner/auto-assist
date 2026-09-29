"""Harness promotion gates (W5): machine-checked operator review inputs.

The harness version registry's promotion rules become gate code. A promotion
decision is an input to operator review — never an auto-promotion.

Gates, in registry order:

- ``suite_registered``     — the suite id exists in the harness suite registry.
- ``trials_met``           — at least ``suite.min_trials`` valid rescore runs
                             (the Hermes qualification rule: three valid trials;
                             a single prompt or one-off probe is not evidence).
- ``runs_ok``              — every rescore run completed and carries a score.
- ``score_floor``          — mean rescore score meets the floor (default 0.8).
- ``no_regression``        — mean rescore score does not regress vs the
                             incumbent (when an incumbent score is provided).
- ``dataset_trace_gate``   — the fine-tune dataset satisfies the trace-first
                             gate: extracted from real sessions (source session
                             ids), redacted, trajectory-deduplicated, with a
                             disjoint held-out split. Manually authored design
                             sketches (e.g. ``hernes_v1_train.jsonl``) never pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evaluation_registry import HarnessSuiteDef, get_harness_suites

DEFAULT_SCORE_FLOOR = 0.8


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)


def _rescore_runs(chain_id: str, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rescore-stage EvaluationRun rows for this chain, normalized."""
    valid: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        metadata = run.get("metadata") or {}
        if not metadata.get("harness_evolution"):
            continue
        if metadata.get("chain_id") != chain_id:
            continue
        if metadata.get("stage") != "rescore":
            continue
        valid.append(run)
    return valid


def dataset_trace_gate(dataset: dict[str, Any] | None) -> tuple[bool, list[str]]:
    """Trace-first dataset gate. Required before any fine-tune promotion:
    extraction from real sessions, redaction, trajectory dedupe, held-out
    split (registry gate items 1-4)."""
    failures: list[str] = []
    if not isinstance(dataset, dict) or not dataset:
        return False, ["no dataset provenance recorded"]
    if not dataset.get("ref"):
        failures.append("dataset ref missing")
    if not dataset.get("source_session_ids"):
        failures.append("not extracted from real sessions (no source session ids)")
    if not dataset.get("redacted"):
        failures.append("secrets not redacted")
    if not dataset.get("deduplicated"):
        failures.append("trajectories not deduplicated")
    if not dataset.get("held_out_split"):
        failures.append("no disjoint held-out split")
    return not failures, failures


def evaluate_promotion(
    chain: Any,
    suite: HarnessSuiteDef,
    runs: list[dict[str, Any]],
    *,
    dataset: dict[str, Any] | None = None,
    incumbent_score: float | None = None,
    score_floor: float = DEFAULT_SCORE_FLOOR,
) -> PromotionDecision:
    """Evaluate a chain's rescore evidence against the promotion gates."""
    registered_ids = {s.id for s in get_harness_suites()}
    reasons: list[str] = []
    checks: dict[str, bool] = {}

    checks["suite_registered"] = suite.id in registered_ids
    if not checks["suite_registered"]:
        reasons.append(f"suite {suite.id} is not in the harness registry")

    chain_runs = _rescore_runs(chain.chain_id, runs or [])
    scored = [
        run for run in chain_runs
        if str(run.get("status") or "").upper() in {"DONE", "COMPLETED", "PASS"}
        and isinstance(run.get("score"), (int, float))
    ]
    checks["trials_met"] = len(scored) >= suite.min_trials
    if not checks["trials_met"]:
        reasons.append(
            f"only {len(scored)} valid rescore run(s); {suite.min_trials} required"
        )
    checks["runs_ok"] = bool(chain_runs) and len(scored) == len(chain_runs)
    if chain_runs and not checks["runs_ok"]:
        reasons.append(
            f"{len(chain_runs) - len(scored)} rescore run(s) incomplete or unscored"
        )

    mean_score = (
        sum(float(run["score"]) for run in scored) / len(scored) if scored else None
    )
    checks["score_floor"] = mean_score is not None and mean_score >= score_floor
    if not checks["score_floor"]:
        reasons.append(
            f"mean rescore score {mean_score if mean_score is not None else 'n/a'} "
            f"below floor {score_floor}"
        )

    if incumbent_score is None:
        checks["no_regression"] = True
    elif mean_score is None:
        checks["no_regression"] = False
        reasons.append("no scored runs to compare against the incumbent")
    else:
        checks["no_regression"] = mean_score >= incumbent_score
        if not checks["no_regression"]:
            reasons.append(
                f"regression: mean {mean_score:.4f} < incumbent {incumbent_score:.4f}"
            )

    gate_ok, gate_failures = dataset_trace_gate(dataset)
    checks["dataset_trace_gate"] = gate_ok
    reasons.extend(gate_failures)

    return PromotionDecision(
        promote=all(checks.values()),
        reasons=reasons,
        checks=checks,
    )
