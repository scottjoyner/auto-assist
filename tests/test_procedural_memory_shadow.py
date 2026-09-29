from assistx.procedural_memory import ProceduralMemoryCandidate
from assistx.procedural_memory_shadow import shadow_match


def _candidate(rule: str, *, positive=4, negative=0, confidence=0.9):
    return ProceduralMemoryCandidate(
        rule=rule,
        source_run_ids=("r1", "r2", "r3", "r4"),
        positive_outcomes=positive,
        negative_outcomes=negative,
        confidence=confidence,
    )


def test_shadow_ranks_relevant_rule_higher_without_activation():
    matches = shadow_match(
        "fix failing tests before broad integration validation",
        [
            _candidate("Run targeted tests before broad integration tests."),
            _candidate("Prefer documentation edits before changing APIs."),
        ],
    )
    assert matches[0].rule.startswith("Run targeted tests")
    assert matches[0].eligible is True


def test_shadow_retains_ineligible_candidate_for_calibration():
    matches = shadow_match(
        "database migration rollback",
        [_candidate("Verify rollback before database migration.", positive=1, negative=3)],
    )
    assert len(matches) == 1
    assert matches[0].eligible is False
    assert matches[0].source_run_ids == ("r1", "r2", "r3", "r4")
