"""Hermetic offline checks for harness-reflection coverage accounting.

Only synthetic in-memory dicts are used. No DB, network, service, credential,
filesystem-state or live-fleet fixture is involved.
"""

from __future__ import annotations

from assistx.harness_reflect import MAX_COMPLETION_CHARS, validate_reflection

FAIL_SET = [{"task_id": "BM-007", "expected": "expected", "actual": "actual", "duration_s": 1.0}]


def _reflection(**overrides):
    reflection = {"correction_pairs": [], "harness_fix_proposals": []}
    reflection.update(overrides)
    return reflection


def _pair(**overrides):
    pair = {
        "fail_id": "BM-007",
        "prompt": "corrected instruction",
        "completion": "corrected output",
    }
    pair.update(overrides)
    return pair


def test_missing_prompt_must_not_mark_fail_as_covered():
    """An empty prompt is a validation error; it cannot cover the failed task."""
    errors, uncovered = validate_reflection(_reflection(correction_pairs=[_pair(prompt="")]), FAIL_SET)
    assert any("missing prompt" in item for item in errors)
    assert uncovered == ["BM-007"]


def test_both_missing_fields_report_both_errors_and_stay_uncovered():
    """Both diagnostics are preserved, yet coverage is still not granted."""
    errors, uncovered = validate_reflection(_reflection(correction_pairs=[{"fail_id": "BM-007"}]), FAIL_SET)
    assert any("missing prompt" in item for item in errors)
    assert any("missing completion" in item for item in errors)
    assert uncovered == ["BM-007"]


def test_valid_pair_still_covers_fail():
    errors, uncovered = validate_reflection(_reflection(correction_pairs=[_pair()]), FAIL_SET)
    assert errors == []
    assert uncovered == []


def test_missing_completion_still_leaves_fail_uncovered():
    errors, uncovered = validate_reflection(_reflection(correction_pairs=[_pair(completion="")]), FAIL_SET)
    assert any("missing completion" in item for item in errors)
    assert uncovered == ["BM-007"]


def test_oversized_completion_still_leaves_fail_uncovered():
    errors, uncovered = validate_reflection(
        _reflection(correction_pairs=[_pair(completion="x" * (MAX_COMPLETION_CHARS + 1))]),
        FAIL_SET,
    )
    assert any("exceeds" in item for item in errors)
    assert uncovered == ["BM-007"]


def test_proposal_missing_title_still_leaves_fail_uncovered():
    errors, uncovered = validate_reflection(
        _reflection(harness_fix_proposals=[{"fail_id": "BM-007", "title": "", "patch_summary": "bounded fix"}]),
        FAIL_SET,
    )
    assert any("missing title" in item for item in errors)
    assert uncovered == ["BM-007"]
