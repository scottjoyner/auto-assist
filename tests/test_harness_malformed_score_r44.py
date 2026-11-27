"""Fine-tune promotion must not certify malformed rescore scores as evidence.

Round 44 (AssistX). ``evaluate_promotion`` previously counted any ``int``-typed
value as a valid score; Python's ``bool`` is an ``int`` subclass, so a score of
``True`` was treated as a perfect 1.0, and an infinite float also cleared the
score floor. Both could drive ``promote=True`` with a clean dataset and no
incumbent, certifying a fine-tune on evidence that did not exist.

Hermetic: in-process dict/SimpleNamespace stubs only. No socket, HTTP, Neo4j,
Redis, SQLite, DB/WAL, credential file, service, container or network mount is
read or written; the root ``tests/conftest.py`` Neo4j fixtures are never used.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from assistx import harness_gates as hg
from assistx.evaluation_registry import HarnessSuiteDef

SUITE_ID = "cpm-tb2-bench-v1"
CHAIN = SimpleNamespace(chain_id="hchain-round44-malformed-score")


def _suite() -> HarnessSuiteDef:
    # A registered suite id: keeps suite_registered=True so only the score
    # evidence gates can decide the outcome.
    return HarnessSuiteDef(id=SUITE_ID, schema_version="test-only")


def _dataset() -> dict[str, object]:
    return {
        "ref": "datasets/train.jsonl",
        "source_session_ids": ["sess-1", "sess-2"],
        "redacted": True,
        "deduplicated": True,
        "held_out_split": True,
    }


def _run(score: object, status: str = "DONE") -> dict[str, object]:
    return {
        "id": "eval-round44",
        "status": status,
        "score": score,
        "metadata": {
            "harness_evolution": True,
            "chain_id": CHAIN.chain_id,
            "stage": "rescore",
        },
    }


def _decide(scores: list[object], **kwargs: object) -> hg.PromotionDecision:
    return hg.evaluate_promotion(
        CHAIN,
        _suite(),
        [_run(score) for score in scores],
        dataset=_dataset(),
        **kwargs,
    )


@pytest.mark.parametrize(
    "bad_score",
    [
        True,                 # bool is an int subclass: pre-fix this was 1.0
        float("inf"),         # infinite float cleared the floor pre-fix
        float("-inf"),
        float("nan"),
        10**400,              # oversized int cannot be converted to a mean
        "1.0",                # string coercion must not become a score
        None,
        [1.0],
    ],
)
def test_malformed_score_is_not_counted_as_a_valid_trial(bad_score: object) -> None:
    decision = _decide([bad_score, bad_score])
    assert decision.checks["trials_met"] is False
    assert decision.checks["runs_ok"] is False
    assert decision.checks["score_floor"] is False
    assert decision.promote is False
    assert any("valid rescore run" in reason for reason in decision.reasons)


def test_bool_true_score_never_promotes_even_with_clean_dataset() -> None:
    decision = _decide([True, True], incumbent_score=0.95)
    assert decision.promote is False
    assert decision.checks["trials_met"] is False
    assert decision.checks["no_regression"] is False


def test_infinite_score_never_promotes_even_with_clean_dataset() -> None:
    decision = _decide([float("inf"), float("inf")], incumbent_score=0.95)
    assert decision.promote is False
    assert decision.checks["score_floor"] is False


@pytest.mark.parametrize("good_score", [1.0, 1, 0, 0.9])
def test_real_numeric_scores_are_still_counted(good_score: object) -> None:
    decision = _decide([good_score, good_score])
    assert decision.checks["trials_met"] is True
    assert decision.checks["runs_ok"] is True


def test_valid_finite_scores_still_promote() -> None:
    decision = _decide([1.0, 0.9], incumbent_score=0.95)
    assert decision.promote is True
    assert decision.reasons == []
    assert all(decision.checks.values())
