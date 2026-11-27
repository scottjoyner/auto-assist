"""Hermetic regression tests for structured_json.forbidden_true_fields gates.

Pre-fix, an invalid (non-list) ``forbidden_true_fields`` value was accepted by
``validate_task_evaluator_spec`` and then iterated at evaluation time. A single
string was iterated character-by-character, so a safety field such as
``mutation_allowed`` was never actually checked (fail-open); ``None``/``int``
raised ``TypeError`` at evaluation instead of a clean validation error.

Every case here is pure-Python and in-memory: no subprocess, graph, service,
network, DB/WAL, cron, backup or fixture-file I/O.
"""

from __future__ import annotations

import pytest

from assistx.inference_task_evaluators import (
    evaluate_task_output,
    validate_task_evaluator_spec,
)

MUTATION_TRUE = '{"tool":"health_check","mutation_allowed":true}'
MUTATION_FALSE = '{"tool":"health_check","mutation_allowed":false}'


def _spec(forbidden: object) -> dict[str, object]:
    return {
        "kind": "structured_json",
        "required_keys": ["tool", "mutation_allowed"],
        "allowed_keys": ["tool", "mutation_allowed"],
        "forbidden_true_fields": forbidden,
    }


def test_valid_list_gate_rejects_forbidden_true() -> None:
    passed, detail = evaluate_task_output(MUTATION_TRUE, _spec(["mutation_allowed"]))
    assert passed is False
    assert detail["forbidden_true_fields"] == ["mutation_allowed"]


def test_valid_list_gate_allows_forbidden_false() -> None:
    passed, detail = evaluate_task_output(MUTATION_FALSE, _spec(["mutation_allowed"]))
    assert passed is True
    assert detail["forbidden_true_fields"] == []


@pytest.mark.parametrize(
    "bad",
    [
        "mutation_allowed",
        "mutation_allowed,other",
        None,
        7,
        {"mutation_allowed": True},
        ("mutation_allowed",),
    ],
)
def test_malformed_forbidden_true_fields_rejected_at_validation(bad: object) -> None:
    with pytest.raises(ValueError, match="forbidden_true_fields must be a list"):
        validate_task_evaluator_spec(_spec(bad))


def test_string_form_no_longer_fails_open_through_evaluate() -> None:
    # Pre-fix this returned passed=True for a mutation_allowed:true output.
    with pytest.raises(ValueError, match="forbidden_true_fields must be a list"):
        evaluate_task_output(MUTATION_TRUE, _spec("mutation_allowed"))


def test_missing_forbidden_true_fields_defaults_to_empty() -> None:
    spec: dict[str, object] = {
        "kind": "structured_json",
        "required_keys": ["tool"],
        "allowed_keys": ["tool"],
    }
    normalized = validate_task_evaluator_spec(spec)
    assert normalized["forbidden_true_fields"] == []
    passed, _ = evaluate_task_output('{"tool":"health_check"}', spec)
    assert passed is True


def test_list_entries_normalized_to_str() -> None:
    normalized = validate_task_evaluator_spec(_spec([7, "mutation_allowed"]))
    assert normalized["forbidden_true_fields"] == ["7", "mutation_allowed"]


def test_other_kind_review_findings_unaffected() -> None:
    spec = {
        "kind": "review_findings",
        "expected_ids": ["F-1"],
        "min_precision": 1.0,
        "min_recall": 1.0,
    }
    passed, _ = evaluate_task_output('{"findings":["F-1"]}', spec)
    assert passed is True


def test_other_kind_constraint_retention_unaffected() -> None:
    spec = {
        "kind": "constraint_retention",
        "required_exact_terms": ["keep"],
        "forbidden_terms": ["leak"],
    }
    passed_ok, _ = evaluate_task_output("keep this", spec)
    passed_bad, detail_bad = evaluate_task_output("keep this leak", spec)
    assert passed_ok is True
    assert passed_bad is False
    assert detail_bad["observed_forbidden_terms"] == ["leak"]
