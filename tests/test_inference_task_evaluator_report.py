from __future__ import annotations

import json
from pathlib import Path

import pytest

from assistx.inference_task_evaluator_report import (
    load_task_evaluator_suite,
    summarize_task_evaluator_results,
)


SUITE_PATH = (
    "examples/assistx-inference-policy-experiment/"
    "task-evaluator.suite.json"
)

CASES_PATH = (
    "examples/assistx-inference-policy-experiment/"
    "task-evaluator.cases.jsonl"
)


def _kinds() -> dict[str, str]:
    kinds: dict[str, str] = {}
    for line in Path(CASES_PATH).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        kinds[row["case_id"]] = row["acceptance"]["task_evaluator"]["kind"]
    return kinds


def _rows(*, failed_case=None, telemetry_invalid_case=None):
    suite = load_task_evaluator_suite(SUITE_PATH)
    rows = []
    for case_id, kind in _kinds().items():
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
    drifted = next(
        row for row in rows if row["case_id"] == "taskq-code-sum-even"
    )
    drifted["case_sha256"] = "0" * 64
    report = summarize_task_evaluator_results(rows, suite)
    policy = report["policies"][0]
    assert policy["eligible_for_training_evidence"] is False
    assert policy["case_hash_mismatch_ids"] == [
        "taskq-code-sum-even"
    ]


def test_widened_authority_blocks_training_evidence():
    suite = load_task_evaluator_suite(SUITE_PATH)
    rows = _rows()
    widened = next(
        row for row in rows if row["case_id"] == "taskq-code-sum-even"
    )
    widened["authority"]["mutation_allowed"] = True
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


def _synthetic(kind: str, count: int, *, failures: int, floor: float):
    """A single-kind suite and rows, so the binomial noise scale is explicit."""
    suite = load_task_evaluator_suite(SUITE_PATH)
    case_ids = [f"synthetic-{kind}-{index:04d}" for index in range(count)]
    hashes = {case_id: f"{index:064x}" for index, case_id in enumerate(case_ids)}
    suite = dict(suite)
    suite["required_case_ids"] = case_ids
    suite["case_sha256_by_id"] = hashes
    suite["minimum_overall_pass_rate"] = floor
    suite["minimum_pass_rate_by_kind"] = {kind: floor}
    rows = []
    for index, case_id in enumerate(case_ids):
        failed = index < failures
        rows.append(
            {
                "evaluation_suite": suite["suite_id"],
                "case_id": case_id,
                "case_sha256": hashes[case_id],
                "evaluator_kind": kind,
                "policy_id": "r9700-q4-mtp",
                "policy_sha256": "b" * 64,
                "node_id": "r9700",
                "model_handle": "qwen3.8-27b-q4",
                "backend": "rocm",
                "quantization": "q4",
                "speculation": "mtp",
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
                "telemetry_valid": True,
            }
        )
    return suite, rows


def test_noise_tolerance_defaults_to_the_strict_floor():
    # 379/400 = 0.9475 misses a 0.95 floor by 0.23 binomial sd.
    suite, rows = _synthetic("python_function", 400, failures=21, floor=0.95)

    report = summarize_task_evaluator_results(rows, suite)

    policy = report["policies"][0]
    assert policy["eligible_for_training_evidence"] is False
    assert report["noise_tolerance_sd"] == 0.0
    kind = policy["by_evaluator_kind"]["python_function"]
    assert kind["effective_minimum"] == kind["required_minimum"] == 0.95
    assert kind["standard_error"] == 0.0


def test_one_sd_tolerance_admits_a_sub_noise_miss():
    suite, rows = _synthetic("python_function", 400, failures=21, floor=0.95)

    report = summarize_task_evaluator_results(rows, suite, noise_tolerance_sd=1.0)

    policy = report["policies"][0]
    kind = policy["by_evaluator_kind"]["python_function"]
    assert kind["passed"] is True
    assert kind["passed_strictly"] is False
    # the floor is 0.95, the miss is 0.23 sd, and one sd of tolerance covers it
    assert kind["effective_minimum"] < 0.9475 < 0.95
    assert -1.0 < kind["margin_sd"] < 0.0
    assert policy["eligible_for_training_evidence"] is True


def test_noise_tolerance_never_admits_a_gap_larger_than_its_noise():
    # 300/400 = 0.75 misses by 18 sd; tolerance must not reach it.
    suite, rows = _synthetic("python_function", 400, failures=100, floor=0.95)

    report = summarize_task_evaluator_results(rows, suite, noise_tolerance_sd=1.0)

    policy = report["policies"][0]
    kind = policy["by_evaluator_kind"]["python_function"]
    assert kind["margin_sd"] < -1.0
    assert kind["passed"] is False
    assert policy["eligible_for_training_evidence"] is False


def test_tolerance_is_recorded_and_changes_the_report_receipt():
    suite, rows = _synthetic("python_function", 400, failures=21, floor=0.95)

    strict = summarize_task_evaluator_results(rows, suite)
    tolerant = summarize_task_evaluator_results(rows, suite, noise_tolerance_sd=1.0)

    assert strict["report_sha256"] != tolerant["report_sha256"]
    assert tolerant["noise_tolerance_sd"] == 1.0
    assert strict["authority"]["routing_authority_changed"] is False
    assert tolerant["production_promotion_authorized"] is False


def test_tolerance_is_covered_by_the_receipt_even_when_it_moves_nothing():
    # A floor of 1.0 has zero binomial variance, so any tolerance leaves every
    # effective minimum identical. The receipt must still distinguish the two
    # reports, or a tolerance could be changed without changing the hash.
    suite, rows = _synthetic("python_function", 50, failures=0, floor=1.0)

    strict = summarize_task_evaluator_results(rows, suite, noise_tolerance_sd=0.0)
    nudged = summarize_task_evaluator_results(rows, suite, noise_tolerance_sd=1e-12)

    assert (
        strict["policies"][0]["by_evaluator_kind"]["python_function"]
        == nudged["policies"][0]["by_evaluator_kind"]["python_function"]
    )
    assert strict["report_sha256"] != nudged["report_sha256"]
