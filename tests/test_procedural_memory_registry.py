import pytest

from assistx.procedural_memory import ProceduralMemoryCandidate
from assistx.procedural_memory_registry import activate_candidate, invalidate, supersede


def _candidate(*, positive=4, negative=0, confidence=0.9):
    return ProceduralMemoryCandidate(
        rule="Run targeted tests before broad integration tests.",
        source_run_ids=("r1", "r2", "r3", "r4"),
        positive_outcomes=positive,
        negative_outcomes=negative,
        confidence=confidence,
    )


def test_ineligible_candidate_cannot_activate():
    with pytest.raises(ValueError, match="not eligible"):
        activate_candidate("m1", _candidate(positive=1, negative=3))


def test_active_record_can_be_invalidated_with_history_preserved():
    record = activate_candidate("m1", _candidate())
    invalid = invalidate(record, "held-out regression")
    assert invalid.active is False
    assert invalid.invalidated_reason == "held-out regression"
    assert invalid.candidate == record.candidate


def test_active_replacement_supersedes_without_deleting_old_record():
    old = activate_candidate("m1", _candidate())
    replacement = activate_candidate("m2", _candidate(confidence=0.95))
    superseded, current = supersede(old, replacement)
    assert superseded.active is False
    assert superseded.superseded_by == "m2"
    assert current.active is True
