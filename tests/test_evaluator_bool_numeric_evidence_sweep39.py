"""Sweep39: python_function pass evidence must not equate booleans with numbers.

``True == 1`` and ``False == 0`` in Python.  A benchmark case whose expected
value is numeric must not be certified as passed by a candidate that returned
a boolean (or vice versa), at any nesting depth.  Every case below is an
in-process, synthetic evaluator call: no socket, service, container, DB or
network mount is used.
"""

from __future__ import annotations

import pytest

from assistx.inference_task_evaluators import evaluate_task_output

BOOL_TRUE = "def f():\n    return True\n"
BOOL_FALSE = "def f():\n    return False\n"
INT_ONE = "def f():\n    return 1\n"
INT_ZERO = "def f():\n    return 0\n"
FLOAT_ONE = "def f():\n    return 1.0\n"
LIST_TRUE = "def f():\n    return [True]\n"
DICT_TRUE = "def f():\n    return {'ok': True}\n"


def _spec(*expected: object) -> dict[str, object]:
    return {
        "kind": "python_function",
        "function_name": "f",
        "tests": [{"args": [], "kwargs": {}, "expected": value} for value in expected],
    }


@pytest.mark.parametrize(
    "code, expected, label",
    [
        (BOOL_TRUE, 1, "bool True must not satisfy an int 1 expectation"),
        (INT_ONE, True, "int 1 must not satisfy a bool True expectation"),
        (BOOL_FALSE, 0, "bool False must not satisfy an int 0 expectation"),
        (INT_ZERO, False, "int 0 must not satisfy a bool False expectation"),
        (LIST_TRUE, [1], "nested True must not satisfy a nested int 1"),
        (DICT_TRUE, {"ok": 1}, "nested True must not satisfy a nested int 1 in a dict"),
    ],
)
def test_boolean_numeric_type_confusion_is_rejected(code, expected, label):
    passed, detail = evaluate_task_output(code, _spec(expected))

    assert passed is False, label
    assert detail["passed"] is False
    assert detail["passed_tests"] == 0
    assert detail["tests"][0]["passed"] is False


@pytest.mark.parametrize(
    "code, expected, label",
    [
        (INT_ONE, 1, "exact int still passes"),
        (BOOL_TRUE, True, "exact bool still passes"),
        (INT_ZERO, 0, "exact zero int still passes"),
        (BOOL_FALSE, False, "exact False bool still passes"),
        (FLOAT_ONE, 1, "numeric int/float equivalence is preserved"),
        (LIST_TRUE, [True], "nested exact bool still passes"),
        (DICT_TRUE, {"ok": True}, "nested exact bool in a dict still passes"),
    ],
)
def test_matching_type_and_numeric_controls_still_pass(code, expected, label):
    passed, detail = evaluate_task_output(code, _spec(expected))

    assert passed is True, label
    assert detail["passed_tests"] == 1
    assert detail["tests"][0]["passed"] is True


def test_mixed_test_report_counts_only_genuinely_matching_cases():
    # Observed True: first expectation (1) is type-confused, second is exact.
    passed, detail = evaluate_task_output(BOOL_TRUE, _spec(1, True))

    assert passed is False
    assert detail["passed_tests"] == 1
    assert detail["total_tests"] == 2
    assert [row["passed"] for row in detail["tests"]] == [False, True]
