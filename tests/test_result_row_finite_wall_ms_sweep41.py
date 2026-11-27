"""Hermetic tests for sweep41: non-finite wall_ms must not pass result-row admission.

Only synthetic in-memory dicts are used. No Docker, service, DB, network,
credential or live-fleet access is performed.
"""

from __future__ import annotations

import pytest

from assistx.inference_policy_experiment import DEFAULT_AUTHORITY, canonical_sha256
from assistx.inference_policy_training_dataset import (
    _record_for_group,
    _validate_result_row,
)

CASE_SHA = "case-sha-1"
POLICY = {"policy_sha256": "policy-sha-1"}


def _row(**overrides):
    row = {
        "case_sha256": CASE_SHA,
        "policy_sha256": "policy-sha-1",
        "success": True,
        "acceptance_passed": True,
        "execution_mode": "observe_only",
        "allow_model_load": False,
        "routing_authority_changed": False,
        "authority": dict(DEFAULT_AUTHORITY),
        "telemetry_required": False,
        "wall_ms": 12.5,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_wall_ms_is_rejected(bad):
    """NaN/+Inf latency is not a measurement and must not be admitted."""
    assert _validate_result_row(_row(wall_ms=bad), policy=POLICY, case_sha256=CASE_SHA) == (False, "wall_ms_invalid")


@pytest.mark.parametrize("bad", [float("-inf"), 0, -1, -0.5, "12.5", None, True])
def test_other_invalid_wall_ms_remains_rejected(bad):
    assert _validate_result_row(_row(wall_ms=bad), policy=POLICY, case_sha256=CASE_SHA) == (False, "wall_ms_invalid")


def test_default_row_is_valid_control():
    assert _validate_result_row(_row(), policy=POLICY, case_sha256=CASE_SHA) == (
        True,
        None,
    )


@pytest.mark.parametrize("good", [1, 12.5, 1_000_000.0])
def test_finite_positive_wall_ms_is_accepted(good):
    assert _validate_result_row(_row(wall_ms=good), policy=POLICY, case_sha256=CASE_SHA) == (True, None)


def _policy(policy_id: str, model: str) -> dict:
    return {
        "policy_id": policy_id,
        "node_id": "node-a",
        "model_handle": model,
        "backend": "openai",
        "quantization": "q4",
        "speculation": "off",
        "context_tokens": 1024,
        "concurrency": 1,
        "policy_sha256": policy_id,
        "telemetry_required": False,
        "execution_mode": "observe_only",
        "allow_model_load": False,
        "authority": dict(DEFAULT_AUTHORITY),
    }


def test_nan_row_cannot_poison_training_candidate_selection():
    """A NaN row must never enter the bundle builder's candidate set.

    Before the fix the NaN row was admitted, and because every comparison with
    NaN is False it won its signature group regardless of the real latency of
    the competing row (and drove the near-best distribution to all zeros).
    """
    policy_a = _policy("pol-a", "model-a")
    policy_b = _policy("pol-b", "model-b")
    case = {
        "case_id": "case-a",
        "case_sha256": CASE_SHA,
        "dataset_group": "group-a",
        "task_family": "reasoning",
        "messages": [{"role": "user", "content": "synthetic prompt"}],
    }
    rows = [
        _row(policy_id="pol-a", policy_sha256="pol-a", wall_ms=float("nan")),
        _row(policy_id="pol-b", policy_sha256="pol-b", wall_ms=25.0),
    ]
    record = _record_for_group(
        case=case,
        context_tokens=1024,
        rows=rows,
        policies={"pol-a": policy_a, "pol-b": policy_b},
        admissible_ids={"pol-a", "pol-b"},
        quality={
            "pol-a": {
                "eligible_for_training_evidence": True,
                "policy_sha256": "pol-a",
            },
            "pol-b": {
                "eligible_for_training_evidence": True,
                "policy_sha256": "pol-b",
            },
        },
        campaign_evidence_sha256=canonical_sha256({"seed": 1}),
        quality_report_sha256=canonical_sha256({"seed": 2}),
        tie_ratio=1.5,
    )
    # With the NaN row rejected, only one admissible signature remains and the
    # builder declines to emit a comparison record.
    assert record is None
