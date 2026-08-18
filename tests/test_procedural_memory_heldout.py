from assistx.procedural_memory import ProceduralMemoryCandidate
from assistx.procedural_memory_heldout import (
    HeldOutTaskOutcome,
    evaluate_heldout_task,
    summarize_heldout,
)


def _candidate(rule: str, positives: int, negatives: int, confidence: float = 0.9):
    return ProceduralMemoryCandidate(
        rule=rule,
        source_run_ids=("run-a", "run-b", "run-c"),
        positive_outcomes=positives,
        negative_outcomes=negatives,
        confidence=confidence,
    )


def test_heldout_observation_preserves_supported_and_contradicted_rules():
    candidates = [
        _candidate("run targeted tests before broad integration tests", 8, 1),
        _candidate("delete state when migrations fail", 7, 1),
    ]
    outcome = HeldOutTaskOutcome(
        task_id="task-1",
        task_text="fix failing tests and validate migration",
        success=True,
        repeated_error=False,
        searches=3,
        verification_retries=1,
        time_to_first_correct_plan_ms=1200,
        supporting_rules=("run targeted tests before broad integration tests",),
        contradicted_rules=("delete state when migrations fail",),
    )
    observation = evaluate_heldout_task(outcome, candidates)
    assert observation.success is True
    assert "run targeted tests before broad integration tests" in observation.eligible_rules
    assert observation.supported_eligible_rules == (
        "run targeted tests before broad integration tests",
    )
    assert "delete state when migrations fail" in observation.contradicted_eligible_rules


def test_bounded_retrieval_limits_eligible_rule_surface():
    candidates = [
        _candidate("run targeted tests before broad integration tests", 8, 1),
        _candidate("inspect failing logs before changing implementation", 8, 1),
        _candidate("review repository configuration before deployment", 8, 1),
    ]
    observation = evaluate_heldout_task(
        HeldOutTaskOutcome(
            task_id="bounded",
            task_text="inspect failing logs and run targeted tests",
            success=True,
            repeated_error=False,
            supporting_rules=(candidates[0].rule, candidates[1].rule),
        ),
        candidates,
        limit=2,
    )
    assert len(observation.retrieved_rules) <= 2
    assert len(observation.eligible_rules) <= 2


def test_summary_calculates_support_and_repeated_error_reduction():
    candidate = _candidate("run targeted tests before broad integration tests", 8, 1)
    observations = [
        evaluate_heldout_task(
            HeldOutTaskOutcome(
                task_id=f"t{i}",
                task_text="run targeted tests for the failure",
                success=True,
                repeated_error=(i == 0),
                supporting_rules=(candidate.rule,),
                searches=2,
            ),
            [candidate],
        )
        for i in range(4)
    ]
    summary = summarize_heldout(observations, baseline_repeated_error_rate=0.5)
    assert summary["eligible_support_rate"] == 1.0
    assert summary["task_success_rate"] == 1.0
    assert summary["repeated_error_rate"] == 0.25
    assert summary["repeated_error_reduction_ratio"] == 0.5
    assert summary["authoritative_behavior_changed"] is False


def test_ineligible_single_success_cannot_count_as_eligible_support():
    lucky = ProceduralMemoryCandidate(
        rule="always retry the same command",
        source_run_ids=("lucky",),
        positive_outcomes=1,
        negative_outcomes=0,
        confidence=0.99,
    )
    observation = evaluate_heldout_task(
        HeldOutTaskOutcome(
            task_id="single",
            task_text="retry command",
            success=False,
            repeated_error=True,
            supporting_rules=(lucky.rule,),
        ),
        [lucky],
    )
    assert observation.eligible_rules == ()
