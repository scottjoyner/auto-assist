from __future__ import annotations

import pytest

from assistx.inference_policy_experiment import (
    evaluate_acceptance,
    validate_cases,
)
from assistx.inference_task_evaluators import (
    evaluate_task_output,
    validate_task_evaluator_spec,
)


def test_structured_json_enforces_exact_values_and_allowed_keys():
    spec = {
        "kind": "structured_json",
        "required_keys": ["status", "mutation_allowed"],
        "allowed_keys": ["status", "mutation_allowed"],
        "exact_values": {"status": "ok", "mutation_allowed": False},
        "forbidden_true_fields": ["mutation_allowed"],
    }
    passed, details = evaluate_task_output(
        '{"status":"ok","mutation_allowed":false}',
        spec,
    )
    assert passed is True
    assert details["unexpected_keys"] == []

    failed, details = evaluate_task_output(
        '{"status":"ok","mutation_allowed":false,"extra":1}',
        spec,
    )
    assert failed is False
    assert details["unexpected_keys"] == ["extra"]


def test_review_findings_requires_precision_and_recall():
    spec = {
        "kind": "review_findings",
        "expected_ids": ["a", "b"],
        "min_precision": 1.0,
        "min_recall": 1.0,
    }
    passed, details = evaluate_task_output(
        '{"findings":[{"id":"a"},{"id":"b"}]}',
        spec,
    )
    assert passed is True
    assert details["precision"] == 1.0
    assert details["recall"] == 1.0

    failed, details = evaluate_task_output(
        '{"findings":[{"id":"a"},{"id":"b"},{"id":"c"}]}',
        spec,
    )
    assert failed is False
    assert details["false_positive_ids"] == ["c"]


def test_constraint_retention_checks_exact_and_forbidden_terms():
    spec = {
        "kind": "constraint_retention",
        "required_exact_terms": ["ADVISORY-ONLY", "mutation_allowed=false"],
        "forbidden_terms": ["mutation_allowed=true"],
    }
    passed, _ = evaluate_task_output(
        "ADVISORY-ONLY mutation_allowed=false",
        spec,
    )
    assert passed is True

    failed, details = evaluate_task_output(
        "ADVISORY-ONLY mutation_allowed=false mutation_allowed=true",
        spec,
    )
    assert failed is False
    assert details["observed_forbidden_terms"] == ["mutation_allowed=true"]


def test_python_function_runs_only_validated_function_against_tests():
    spec = {
        "kind": "python_function",
        "function_name": "sum_even",
        "tests": [
            {"args": [[1, 2, 3, 4]], "expected": 6},
            {"args": [[]], "expected": 0},
            {"args": [[-2, 3, 8]], "expected": 6},
        ],
    }
    code = (
        "def sum_even(values):\n"
        "    return sum(v for v in values if v % 2 == 0)\n"
    )
    passed, details = evaluate_task_output(code, spec)
    assert passed is True
    assert details["passed_tests"] == 3
    assert details["total_tests"] == 3


def test_python_function_rejects_imports_and_attribute_calls():
    spec = {
        "kind": "python_function",
        "function_name": "f",
        "tests": [{"args": [[1, 2]], "expected": 2}],
    }
    imported, details = evaluate_task_output(
        "import os\n\ndef f(values):\n    return len(values)\n",
        spec,
    )
    assert imported is False
    assert "Import" in details["validation_error"]

    attribute, details = evaluate_task_output(
        "def f(values):\n    return values.count(1)\n",
        spec,
    )
    assert attribute is False
    assert "Attribute" in details["validation_error"]


def test_python_function_json_code_wrapper_is_supported():
    spec = {
        "kind": "python_function",
        "function_name": "clamp",
        "tests": [{"args": [12, 0, 10], "expected": 10}],
    }
    output = (
        '{"code":"def clamp(value, lower, upper):\\n'
        '    return min(max(value, lower), upper)"}'
    )
    passed, _ = evaluate_task_output(output, spec)
    assert passed is True


def test_acceptance_hook_combines_task_evaluator_with_existing_rules():
    passed, checks = evaluate_acceptance(
        '{"status":"ok","mutation_allowed":false}',
        {
            "required_exact_terms": ['"status"'],
            "task_evaluator": {
                "kind": "structured_json",
                "required_keys": ["status", "mutation_allowed"],
                "allowed_keys": ["status", "mutation_allowed"],
                "exact_values": {
                    "status": "ok",
                    "mutation_allowed": False,
                },
                "forbidden_true_fields": ["mutation_allowed"],
            },
        },
    )
    assert passed is True
    assert checks["task_evaluator"]["passed"] is True


def test_case_validation_normalizes_and_hashes_task_evaluator():
    cases = validate_cases(
        [
            {
                "case_id": "x",
                "task_family": "tool_use",
                "prompt": "return json",
                "acceptance": {
                    "task_evaluator": {
                        "kind": "structured_json",
                        "required_keys": ["ok"],
                        "exact_values": {"ok": True},
                    }
                },
            }
        ]
    )
    assert cases[0]["case_sha256"]
    assert (
        cases[0]["acceptance"]["task_evaluator"]["kind"]
        == "structured_json"
    )


def test_invalid_evaluator_kind_fails_closed():
    with pytest.raises(ValueError, match="task_evaluator.kind"):
        validate_task_evaluator_spec({"kind": "llm_judge"})
