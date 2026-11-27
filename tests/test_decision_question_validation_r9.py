"""Round-9 fail-closed validation invariants for System-One decision receipts.

Covers the ``DecisionQuestionResult`` field/model validators and the
``DecisionProvider`` / ``DecisionAuthority`` field constraints in
``assistx.system_one_receipt``. These are pure Pydantic models: no network, no
live DB, no service. They complement (and do not duplicate) the binding /
authority-widening tests in ``test_system_one_receipt.py`` and the canonical
digest drift guards in ``test_system_one_receipt_canonical_r8.py``.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from assistx.system_one_receipt import (
    DecisionAuthority,
    DecisionProvider,
    DecisionQuestionResult,
)


def _question(**overrides):
    base = {
        "name": "route",
        "type": "choice",
        "options": ["chat", "act"],
        "probabilities": [0.9, 0.1],
        "choice": "chat",
        "confidence": 0.9,
        "noul": None,
        "score": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Valid inputs are accepted and round-trip
# ---------------------------------------------------------------------------


def test_valid_choice_question_round_trips():
    q = DecisionQuestionResult.model_validate(_question())
    dumped = q.model_dump(mode="json")
    assert dumped["type"] == "choice"
    assert dumped["choice"] == "chat"
    assert dumped["noul"] is None and dumped["score"] is None


def test_valid_noul_question_round_trips():
    q = DecisionQuestionResult.model_validate(
        _question(
            type="noul",
            options=["false", "true"],
            probabilities=[0.2, 0.8],
            choice="true",
            confidence=0.8,
            noul=0.8,
        )
    )
    assert q.noul == pytest.approx(0.8)
    assert q.score is None


def test_valid_score_question_round_trips():
    q = DecisionQuestionResult.model_validate(
        _question(
            type="score",
            probabilities=[0.5, 0.5],
            confidence=0.5,
            score=3.0,
        )
    )
    assert q.score == pytest.approx(3.0)
    assert q.noul is None


# ---------------------------------------------------------------------------
# Field-level validators: options and probabilities
# ---------------------------------------------------------------------------


def test_question_type_must_be_known():
    with pytest.raises(ValidationError):
        DecisionQuestionResult.model_validate(_question(type="unknown"))


def test_duplicate_options_rejected():
    with pytest.raises(ValidationError, match="must be unique"):
        DecisionQuestionResult.model_validate(_question(options=["chat", "chat"], probabilities=[0.5, 0.5]))


def test_empty_option_rejected():
    with pytest.raises(ValidationError, match="must be non-empty"):
        DecisionQuestionResult.model_validate(_question(options=["chat", ""], probabilities=[0.5, 0.5]))


@pytest.mark.parametrize("bad_prob", [math.nan, math.inf, -math.inf])
def test_non_finite_probability_rejected(bad_prob):
    with pytest.raises(ValidationError, match="must be finite"):
        DecisionQuestionResult.model_validate(_question(probabilities=[bad_prob, 0.1]))


@pytest.mark.parametrize("bad_probs", [[-0.1, 1.1], [1.2, -0.2]])
def test_out_of_range_probability_rejected(bad_probs):
    with pytest.raises(ValidationError):
        DecisionQuestionResult.model_validate(_question(probabilities=bad_probs))


def test_probabilities_must_sum_to_one():
    with pytest.raises(ValidationError, match="must sum to 1"):
        DecisionQuestionResult.model_validate(_question(probabilities=[0.5, 0.4], confidence=0.5))


def test_probability_sum_tolerance_within_onee_minus5_accepted():
    q = DecisionQuestionResult.model_validate(_question(probabilities=[0.5, 0.499999], confidence=0.5))
    assert q.probabilities == pytest.approx([0.5, 0.499999])


def test_option_probability_cardinality_mismatch_rejected():
    with pytest.raises(ValidationError, match="cardinality mismatch"):
        DecisionQuestionResult.model_validate(_question(options=["a", "b", "c"], probabilities=[0.5, 0.5]))


# ---------------------------------------------------------------------------
# Model-level validators: choice / confidence consistency
# ---------------------------------------------------------------------------


def test_choice_must_be_one_of_the_options():
    with pytest.raises(ValidationError, match="choice must be one of"):
        DecisionQuestionResult.model_validate(_question(choice="other"))


def test_choice_must_be_maximum_probability_option():
    with pytest.raises(ValidationError, match="maximum-probability option"):
        DecisionQuestionResult.model_validate(_question(probabilities=[0.1, 0.9], choice="chat", confidence=0.1))


def test_confidence_must_match_selected_probability():
    with pytest.raises(ValidationError, match="confidence must match"):
        DecisionQuestionResult.model_validate(_question(confidence=0.5))


# ---------------------------------------------------------------------------
# Type-specific invariants
# ---------------------------------------------------------------------------


def test_noul_requires_false_true_options():
    with pytest.raises(ValidationError, match="false/true"):
        DecisionQuestionResult.model_validate(
            _question(
                type="noul",
                noul=0.9,
            )
        )


def test_noul_requires_true_probability():
    with pytest.raises(ValidationError, match="requires the true probability"):
        DecisionQuestionResult.model_validate(
            _question(
                type="noul",
                options=["false", "true"],
                choice="false",
            )
        )


def test_noul_must_match_probability_true():
    with pytest.raises(ValidationError, match="noul must match P\\(true\\)"):
        DecisionQuestionResult.model_validate(
            _question(
                type="noul",
                options=["false", "true"],
                probabilities=[0.2, 0.8],
                choice="true",
                confidence=0.8,
                noul=0.3,
            )
        )


def test_noul_result_cannot_carry_score():
    with pytest.raises(ValidationError, match="cannot carry a score"):
        DecisionQuestionResult.model_validate(
            _question(
                type="noul",
                options=["false", "true"],
                probabilities=[0.2, 0.8],
                choice="true",
                confidence=0.8,
                noul=0.8,
                score=1.0,
            )
        )


def test_score_result_requires_finite_score():
    with pytest.raises(ValidationError, match="requires a finite score"):
        DecisionQuestionResult.model_validate(_question(type="score"))
    with pytest.raises(ValidationError, match="requires a finite score"):
        DecisionQuestionResult.model_validate(_question(type="score", score=math.inf))


def test_score_result_cannot_carry_noul():
    with pytest.raises(ValidationError, match="cannot carry noul"):
        DecisionQuestionResult.model_validate(
            _question(
                type="score",
                score=1.0,
                noul=0.5,
            )
        )


@pytest.mark.parametrize("extra", ["noul", "score"])
def test_choice_result_cannot_carry_noul_or_score(extra):
    with pytest.raises(ValidationError, match="cannot carry noul/score"):
        DecisionQuestionResult.model_validate(_question(**{extra: 0.5}))


# ---------------------------------------------------------------------------
# Authority / provider constraints (fail closed, observation only)
# ---------------------------------------------------------------------------


def test_authority_is_all_false_by_default():
    authority = DecisionAuthority()
    assert authority.model_dump() == {
        "dispatch_allowed": False,
        "approval_granted": False,
        "claim_acquired": False,
        "mutation_allowed": False,
        "routing_authority_changed": False,
    }


def test_authority_rejects_widened_and_unknown_fields():
    with pytest.raises(ValidationError):
        DecisionAuthority.model_validate({"dispatch_allowed": True})
    with pytest.raises(ValidationError):
        DecisionAuthority.model_validate({"unknown_authority": False})


def test_provider_requires_nonempty_identity_fields():
    good = {
        "provider_id": "p",
        "provider_version": "v",
        "model_id": "m",
    }
    DecisionProvider.model_validate(good)
    for field in ("provider_id", "provider_version", "model_id"):
        bad = dict(good)
        bad[field] = ""
        with pytest.raises(ValidationError):
            DecisionProvider.model_validate(bad)


def test_provider_artifact_sha_must_be_lowercase_64_hex():
    base = {"provider_id": "p", "provider_version": "v", "model_id": "m"}
    DecisionProvider.model_validate({**base, "model_artifact_sha256": "a" * 64})
    for bad_sha in ("A" * 64, "abc", "z" * 64):
        with pytest.raises(ValidationError):
            DecisionProvider.model_validate({**base, "model_artifact_sha256": bad_sha})


def test_provider_runtime_defaults_to_empty_dict():
    p = DecisionProvider.model_validate({"provider_id": "p", "provider_version": "v", "model_id": "m"})
    assert p.runtime == {}
    assert p.model_version is None and p.model_artifact_sha256 is None
