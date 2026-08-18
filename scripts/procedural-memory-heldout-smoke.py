#!/usr/bin/env python3
"""Generate deterministic held-out procedural-memory evidence."""

from __future__ import annotations

import json
from pathlib import Path

from assistx.procedural_memory import ProceduralMemoryCandidate
from assistx.procedural_memory_heldout import (
    HeldOutTaskOutcome,
    evaluate_heldout_task,
    summarize_heldout,
)


def candidate(rule: str, positives: int, negatives: int, confidence: float = 0.9):
    return ProceduralMemoryCandidate(
        rule=rule,
        source_run_ids=("r1", "r2", "r3", "r4"),
        positive_outcomes=positives,
        negative_outcomes=negatives,
        confidence=confidence,
    )


def main() -> int:
    targeted = candidate("run targeted tests before broad integration tests", 9, 1)
    inspect = candidate("inspect failing logs before changing implementation", 8, 1)
    invalid = candidate("delete state when migrations fail", 8, 1)
    lucky = ProceduralMemoryCandidate(
        rule="retry the same failing command repeatedly",
        source_run_ids=("lucky",),
        positive_outcomes=1,
        negative_outcomes=0,
        confidence=0.99,
    )
    candidates = [targeted, inspect, invalid, lucky]

    outcomes = [
        HeldOutTaskOutcome(
            task_id="heldout-1",
            task_text="fix failing tests by inspecting logs and running targeted tests",
            success=True,
            repeated_error=False,
            searches=2,
            verification_retries=1,
            time_to_first_correct_plan_ms=900,
            supporting_rules=(targeted.rule, inspect.rule),
            contradicted_rules=(invalid.rule,),
        ),
        HeldOutTaskOutcome(
            task_id="heldout-2",
            task_text="debug migration regression with targeted tests",
            success=True,
            repeated_error=False,
            searches=3,
            verification_retries=1,
            time_to_first_correct_plan_ms=1100,
            supporting_rules=(targeted.rule,),
            contradicted_rules=(invalid.rule,),
        ),
        HeldOutTaskOutcome(
            task_id="heldout-3",
            task_text="inspect logs for flaky worker failure",
            success=True,
            repeated_error=False,
            searches=2,
            verification_retries=0,
            time_to_first_correct_plan_ms=800,
            supporting_rules=(inspect.rule,),
        ),
        HeldOutTaskOutcome(
            task_id="heldout-4",
            task_text="retry failing command and inspect logs",
            success=False,
            repeated_error=True,
            searches=5,
            verification_retries=3,
            time_to_first_correct_plan_ms=2600,
            supporting_rules=(inspect.rule,),
            contradicted_rules=(lucky.rule,),
        ),
    ]
    observations = [evaluate_heldout_task(outcome, candidates) for outcome in outcomes]
    payload = summarize_heldout(observations, baseline_repeated_error_rate=0.5)
    payload["results"] = [
        {
            "task_id": o.task_id,
            "eligible_rules": list(o.eligible_rules),
            "supported_eligible_rules": list(o.supported_eligible_rules),
            "contradicted_eligible_rules": list(o.contradicted_eligible_rules),
            "success": o.success,
            "repeated_error": o.repeated_error,
        }
        for o in observations
    ]
    target = Path("procedural-memory-heldout-evidence.json")
    target.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
