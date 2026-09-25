from assistx.procedural_memory import ProceduralMemoryCandidate, eligible_for_promotion
from assistx.procedural_memory_registry import activate_candidate, invalidate, supersede


def _candidate(*, positive, negative, confidence, rule="Always retry until success."):
    return ProceduralMemoryCandidate(
        rule=rule,
        source_run_ids=("r1", "r2", "r3", "r4"),
        positive_outcomes=positive,
        negative_outcomes=negative,
        confidence=confidence,
    )


def test_single_success_high_confidence_does_not_promote():
    candidate = ProceduralMemoryCandidate(
        rule="Delete stale state automatically.",
        source_run_ids=("r1",),
        positive_outcomes=1,
        negative_outcomes=0,
        confidence=0.99,
    )
    assert eligible_for_promotion(candidate) is False


def test_conflicting_outcomes_block_poisoned_rule():
    assert eligible_for_promotion(_candidate(positive=2, negative=2, confidence=0.99)) is False


def test_high_support_bad_rule_can_be_invalidated_without_erasure():
    record = activate_candidate(
        "mem-old",
        _candidate(
            positive=4,
            negative=0,
            confidence=0.95,
            rule="Run targeted tests before broad integration tests.",
        ),
    )
    invalid = invalidate(record, "later held-out evidence contradicted rule")
    assert invalid.active is False
    assert invalid.candidate.source_run_ids == record.candidate.source_run_ids


def test_superseded_rule_remains_traceable():
    old = activate_candidate(
        "mem-old",
        _candidate(positive=4, negative=0, confidence=0.9, rule="Check one test first."),
    )
    new = activate_candidate(
        "mem-new",
        _candidate(positive=4, negative=0, confidence=0.95, rule="Run targeted test group first."),
    )
    replaced, active = supersede(old, new)
    assert replaced.active is False
    assert replaced.superseded_by == "mem-new"
    assert active.memory_id == "mem-new"
