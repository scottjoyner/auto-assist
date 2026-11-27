"""Round-55 regression: harness reflect must fail closed on unlabeled/malformed
`passed` evidence.

`fail_set_from_run()` is the gate between a benchmark run artifact and the
reflect task.  Only an affirmative boolean ``True`` may count as a confirmed
pass; anything else (False, 0, "false", None, or a missing key) is unproven and
must land in the fail set, otherwise a failed task silently disappears from
the reflect prompt and the reflection can appear complete while a real failure
was never addressed.
"""

import pytest

from assistx import harness_reflect as hr


@pytest.fixture()
def mixed_run():
    return {
        "suite_id": "cpm-tb2-bench-v1",
        "harness_id": "cpm-tb2-bench-v1",
        "endpoint": "optiplex:1235",
        "results": [
            {"task_id": "BM-001", "passed": True, "duration_s": 9.0},
            {"task_id": "BM-002", "passed": False, "expected": "e", "actual": "a"},
            {"task_id": "BM-003", "passed": 0, "expected": "e", "actual": "a"},
            {"task_id": "BM-004", "passed": "false", "expected": "e", "actual": "a"},
            {"task_id": "BM-005", "passed": None, "expected": "e", "actual": "a"},
            {"task_id": "BM-006", "expected": "e", "actual": "a"},
        ],
    }


def test_unlabeled_and_malformed_passed_are_failures(mixed_run):
    fails = hr.fail_set_from_run(mixed_run)
    assert [f["task_id"] for f in fails] == [
        "BM-002", "BM-003", "BM-004", "BM-005", "BM-006",
    ]
    assert all(f["expected"] == "e" and f["actual"] == "a" for f in fails)


def test_only_affirmative_true_is_a_confirmed_pass():
    run = {
        "results": [
            {"task_id": "OK-1", "passed": True},
            {"task_id": "OK-2", "passed": 1},
            {"task_id": "OK-3", "passed": "true"},
        ]
    }
    fails = hr.fail_set_from_run(run)
    assert [f["task_id"] for f in fails] == ["OK-2", "OK-3"]


def test_build_reflect_task_does_not_drop_unlabeled_failures(mixed_run):
    # All six entries have a task_id; five are unproven.  A reflect task must
    # still be built so those failures get addressed.
    task = hr.build_reflect_task(mixed_run, target_agent_id="optiplex")
    assert len(task["payload"]["fail_set"]) == 5


def test_all_confirmed_passes_still_raise():
    run = {"results": [{"task_id": "OK-1", "passed": True}]}
    with pytest.raises(ValueError):
        hr.build_reflect_task(run, target_agent_id="optiplex")
