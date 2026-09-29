from __future__ import annotations

import pytest

from assistx.inference_task_evaluator_report import (
    load_task_evaluator_suite,
    summarize_task_evaluator_results,
)


SUITE_PATH = (
    "examples/assistx-inference-policy-experiment/"
    "task-evaluator.suite.json"
)

KINDS = {
    "taskq-code-sum-even": "python_function",
    "taskq-code-clamp": "python_function",
    "taskq-tool-health-json": "structured_json",
    "taskq-tool-route-json": "structured_json",
    "taskq-review-http-shell": "review_findings",
    "taskq-review-file-config": "review_findings",
    "taskq-context-runtime-boundary": "constraint_retention",
    "taskq-context-authority-boundary": "constraint_retention",
}


def _rows(*, failed_case=None, telemetry_invalid_case=None):
    suite = load_task_evaluator_suite(SUITE_PATH)
    rows = []
    for case_id, kind in KINDS.items():
        failed = case_id == failed_case
        telemetry_invalid = case_id == telemetry_invalid_case
        rows.append(
            {
                "evaluation_suite": "assistx_task_quality.v1",
                "case_id": case_id,
                "case_sha256": suite["case_sha256_by_id"][case_id],
                "evaluator_kind": kind,
                "policy_id": "r9700-q4-dflash",
                "policy_sha256": "a" * 64,
                "node_id": "r9700",
                "model_handle": "qwen3.8-27b-q4",
                "backend": "rocm",
                "quantization": "q4",
                "speculation": "dflash",
                "success": not failed,
                "acceptance_passed": not failed,
                "execution_mode": "observe_only",
                "allow_model_load": False,
                "routing_authority_changed": False,
                "authority": {
                    "dispatch_allowed": False,
                    "approval_granted": False,
                    "claim_acquired": False,
                    "mutation_allowed": False,
                    "routing_authority_changed": False,
                },
                "telemetry_required": True,
                "telemetry_valid": not telemetry_invalid,
            }
        )
    return rows


def test_complete_quality_suite_is_training_evidence_eligible():
    suite = load_task_evaluator_suite(SUITE_PATH)
    report = summarize_task_evaluator_results(_rows(), suite)

    assert report["policy_count"] == 1
    policy = report["policies"][0]
    assert policy["eligible_for_training_evidence"] is True
    assert policy["overall_pass_rate"] == 1.0
    assert policy["missing_case_ids"] == []
    assert report["production_promotion_authorized"] is False
    assert report["routing_authority_changed"] is False


def test_failed_case_blocks_training_evidence():
    suite = load_task_evaluator_suite(SUITE_PATH)
    report = summarize_task_evaluator_results(
        _rows(failed_case="taskq-review-http-shell"),
        suite,
    )
    policy = report["policies"][0]
    assert policy["eligible_for_training_evidence"] is False
    assert policy["failed_case_ids"] == ["taskq-review-http-shell"]
    assert policy["by_evaluator_kind"]["review_findings"]["passed"] is False


def test_invalid_telemetry_blocks_case_even_when_acceptance_passes():
    suite = load_task_evaluator_suite(SUITE_PATH)
    report = summarize_task_evaluator_results(
        _rows(telemetry_invalid_case="taskq-code-clamp"),
        suite,
    )
    policy = report["policies"][0]
    assert policy["eligible_for_training_evidence"] is False
    assert "taskq-code-clamp" in policy["failed_case_ids"]


def test_case_hash_drift_blocks_training_evidence():
    suite = load_task_evaluator_suite(SUITE_PATH)
    rows = _rows()
    rows[0]["case_sha256"] = "0" * 64
    report = summarize_task_evaluator_results(rows, suite)
    policy = report["policies"][0]
    assert policy["eligible_for_training_evidence"] is False
    assert policy["case_hash_mismatch_ids"] == [
        "taskq-code-sum-even"
    ]


def test_widened_authority_blocks_training_evidence():
    suite = load_task_evaluator_suite(SUITE_PATH)
    rows = _rows()
    rows[0]["authority"]["mutation_allowed"] = True
    report = summarize_task_evaluator_results(rows, suite)
    policy = report["policies"][0]
    assert policy["eligible_for_training_evidence"] is False
    assert "taskq-code-sum-even" in policy["failed_case_ids"]


def test_missing_case_blocks_complete_suite():
    suite = load_task_evaluator_suite(SUITE_PATH)
    rows = [
        row for row in _rows()
        if row["case_id"] != "taskq-context-authority-boundary"
    ]
    report = summarize_task_evaluator_results(rows, suite)
    policy = report["policies"][0]
    assert policy["eligible_for_training_evidence"] is False
    assert policy["missing_case_ids"] == [
        "taskq-context-authority-boundary"
    ]


def test_duplicate_case_policy_evidence_is_rejected():
    suite = load_task_evaluator_suite(SUITE_PATH)
    rows = _rows()
    rows.append(dict(rows[0]))
    with pytest.raises(ValueError, match="duplicate task-evaluator result"):
        summarize_task_evaluator_results(rows, suite)


def test_suite_loader_hash_is_stable_and_all_false_authority_is_preserved():
    suite = load_task_evaluator_suite(SUITE_PATH)
    assert len(suite["suite_sha256"]) == 64
    report = summarize_task_evaluator_results(_rows(), suite)
    assert all(value is False for value in report["authority"].values())
    assert len(report["report_sha256"]) == 64
