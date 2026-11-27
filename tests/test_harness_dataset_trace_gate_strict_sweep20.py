"""Sweep20: the trace-first fine-tune promotion gate must not accept
non-boolean provenance claims.

Hermetic: in-process dicts only. No socket, HTTP, Neo4j, Redis, SQLite, DB,
credential file, recovery-hold file, docker, or network mount is read. The
root ``tests/conftest.py`` Neo4j integration fixtures are never requested.
"""

from types import SimpleNamespace

import pytest

from assistx import harness_gates as hg
from assistx.evaluation_registry import (
    HarnessSuiteDef,
    get_harness_suites,
)

_PROVENANCE_FLAGS = (
    ("redacted", "secrets not redacted"),
    ("deduplicated", "trajectories not deduplicated"),
    ("held_out_split", "no disjoint held-out split"),
)

# Non-empty but non-boolean values: Python truthiness treats all of these as
# True, so the current ``if not dataset.get(flag)`` gate accepts them.
_TRUTHY_NON_BOOL = (
    "false",
    "0",
    "no",
    "off",
    1,
    ["x"],
)


def _dataset(**overrides):
    data = {
        "ref": "auto-finetune/datasets/train.example.jsonl",
        "source_session_ids": ["hermes-sess-1", "hermes-sess-2"],
        "redacted": True,
        "deduplicated": True,
        "held_out_split": True,
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize("field,reason", _PROVENANCE_FLAGS)
@pytest.mark.parametrize("bad_value", _TRUTHY_NON_BOOL)
def test_truthy_non_boolean_provenance_flag_is_rejected(field, reason, bad_value):
    ok, failures = hg.dataset_trace_gate(_dataset(**{field: bad_value}))
    assert ok is False
    assert reason in failures


@pytest.mark.parametrize("bad_value", _TRUTHY_NON_BOOL)
def test_all_flags_as_non_booleans_do_not_pass_gate(bad_value):
    ok, failures = hg.dataset_trace_gate(
        _dataset(
            redacted=bad_value,
            deduplicated=bad_value,
            held_out_split=bad_value,
        )
    )
    assert ok is False
    assert len(failures) == 3


def test_end_to_end_promotion_is_blocked_by_string_denials():
    """A string-denied dataset must not reach a promote=True verdict."""
    suite = HarnessSuiteDef(
        id=get_harness_suites()[0].id,
        schema_version="x",
        min_trials=1,
    )
    chain = SimpleNamespace(chain_id="sweep20-chain")
    runs = [
        {
            "id": "eval-1",
            "status": "DONE",
            "score": 0.95,
            "metadata": {
                "harness_evolution": True,
                "chain_id": "sweep20-chain",
                "stage": "rescore",
            },
        }
    ]
    dataset = _dataset(redacted="false", deduplicated="0", held_out_split="no")
    decision = hg.evaluate_promotion(chain, suite, runs, dataset=dataset)
    assert decision.checks["dataset_trace_gate"] is False
    assert decision.promote is False


# --- controls: behaviour that must remain unchanged -------------------------


def test_genuine_boolean_true_flags_still_pass():
    ok, failures = hg.dataset_trace_gate(_dataset())
    assert ok is True
    assert failures == []


@pytest.mark.parametrize("field,reason", _PROVENANCE_FLAGS)
def test_explicit_boolean_false_still_fails(field, reason):
    ok, failures = hg.dataset_trace_gate(_dataset(**{field: False}))
    assert ok is False
    assert reason in failures


@pytest.mark.parametrize("dataset", (None, {}, ""))
def test_missing_or_empty_dataset_still_fails(dataset):
    ok, failures = hg.dataset_trace_gate(dataset)
    assert ok is False
    assert failures == ["no dataset provenance recorded"]


def test_falsy_non_bool_values_still_fail():
    ok, failures = hg.dataset_trace_gate(
        _dataset(redacted=0, deduplicated="", held_out_split=None)
    )
    assert ok is False
    assert len(failures) == 3


def test_valid_dataset_still_promotes_end_to_end():
    suite = HarnessSuiteDef(
        id=get_harness_suites()[0].id,
        schema_version="x",
        min_trials=1,
    )
    chain = SimpleNamespace(chain_id="sweep20-chain")
    runs = [
        {
            "id": "eval-1",
            "status": "DONE",
            "score": 0.95,
            "metadata": {
                "harness_evolution": True,
                "chain_id": "sweep20-chain",
                "stage": "rescore",
            },
        }
    ]
    decision = hg.evaluate_promotion(
        chain, suite, runs, dataset=_dataset(), incumbent_score=0.9
    )
    assert decision.promote is True
    assert decision.checks["dataset_trace_gate"] is True
