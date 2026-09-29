import pytest

from assistx.procedural_memory import (
    ProceduralMemoryCandidate,
    eligible_for_promotion,
    validate_candidate,
)


def _candidate(**overrides):
    values = {
        "rule": "Run targeted tests before broad integration tests.",
        "source_run_ids": ("run-1", "run-2", "run-3", "run-4"),
        "positive_outcomes": 4,
        "negative_outcomes": 0,
        "confidence": 0.9,
    }
    values.update(overrides)
    return ProceduralMemoryCandidate(**values)


def test_supported_successful_rule_is_eligible():
    assert eligible_for_promotion(_candidate()) is True


def test_single_success_is_not_enough_support():
    candidate = _candidate(source_run_ids=("run-1",), positive_outcomes=1)
    assert eligible_for_promotion(candidate) is False


def test_negative_outcomes_can_block_promotion():
    candidate = _candidate(positive_outcomes=2, negative_outcomes=2)
    assert candidate.success_rate == 0.5
    assert eligible_for_promotion(candidate) is False


def test_low_confidence_blocks_promotion():
    assert eligible_for_promotion(_candidate(confidence=0.5)) is False


def test_missing_provenance_is_invalid():
    candidate = _candidate(source_run_ids=())
    with pytest.raises(ValueError, match="provenance"):
        validate_candidate(candidate)
