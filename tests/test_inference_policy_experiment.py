from __future__ import annotations

import json

import pytest

from assistx.inference_policy_experiment import (
    compile_trials,
    evaluate_acceptance,
    shadow_rows_to_cases,
    summarize_counterfactuals,
    validate_cases,
    validate_policy,
)


def _policy(**overrides):
    value = {
        "policy_id": "x1-q4-none",
        "node_id": "x1-370",
        "model_handle": "qwen3.8-27b-q4",
        "backend": "vulkan",
        "quantization": "q4",
        "speculation": "none",
        "context_tokens": 8192,
        "concurrency": 1,
        "endpoint_env": "ASSISTX_BENCH_X1_Q4_NONE_URL",
        "execution_mode": "observe_only",
        "allow_model_load": False,
    }
    value.update(overrides)
    return value


def test_policy_rejects_any_model_load_authority():
    with pytest.raises(ValueError, match="allow_model_load"):
        validate_policy(_policy(allow_model_load=True))


def test_policy_rejects_runtime_authority_widening():
    with pytest.raises(ValueError, match="dispatch_allowed"):
        validate_policy(
            _policy(
                authority={
                    "dispatch_allowed": True,
                }
            )
        )


def test_compile_trials_binds_case_and_policy_hashes():
    cases = validate_cases(
        [
            {
                "case_id": "coding-1",
                "task_family": "coding",
                "prompt": "Return JSON with ok=true.",
                "acceptance": {"required_terms": ["ok"]},
            }
        ]
    )
    policy = validate_policy(_policy())
    trials = compile_trials(cases, {"policies": [policy]})

    assert len(trials) == 1
    trial = trials[0]
    assert trial.trial_id.startswith("trial-")
    assert trial.case["case_sha256"]
    assert trial.policy["policy_sha256"]
    assert trial.policy["authority"]["mutation_allowed"] is False


def test_acceptance_requires_all_terms_and_json_keys():
    passed, checks = evaluate_acceptance(
        json.dumps({"ok": True, "node": "x1-370"}),
        {
            "required_terms": ["x1-370"],
            "json_required_keys": ["ok", "node"],
        },
    )
    assert passed is True
    assert checks["required_terms"]["passed"] is True
    assert checks["json_required_keys"]["passed"] is True

    failed, checks = evaluate_acceptance(
        json.dumps({"ok": True}),
        {"json_required_keys": ["ok", "node"]},
    )
    assert failed is False
    assert checks["json_required_keys"]["missing"] == ["node"]


def test_unscored_case_does_not_become_counterfactual_winner():
    summary = summarize_counterfactuals(
        [
            {
                "case_id": "a",
                "policy_id": "fast-unscored",
                "success": True,
                "acceptance_passed": None,
                "wall_ms": 100,
            },
            {
                "case_id": "a",
                "policy_id": "slower-accepted",
                "success": True,
                "acceptance_passed": True,
                "wall_ms": 200,
            },
        ]
    )

    row = summary["cases"][0]
    assert row["best_observed_policy_id"] == "slower-accepted"
    assert row["routing_authority_changed"] is False


def test_counterfactual_speedup_uses_only_accepted_policies():
    summary = summarize_counterfactuals(
        [
            {
                "case_id": "a",
                "policy_id": "baseline",
                "success": True,
                "acceptance_passed": True,
                "wall_ms": 400,
            },
            {
                "case_id": "a",
                "policy_id": "fast-failed-quality",
                "success": True,
                "acceptance_passed": False,
                "wall_ms": 50,
            },
            {
                "case_id": "a",
                "policy_id": "best",
                "success": True,
                "acceptance_passed": True,
                "wall_ms": 200,
            },
        ],
        baseline_policy_id="baseline",
    )

    row = summary["cases"][0]
    assert row["best_observed_policy_id"] == "best"
    assert row["baseline_to_best_speedup"] == 2.0
    assert row["selected_policy_score"] == 2.5
    assert row["best_observed_policy_score"] == 5.0


def test_shadow_rows_become_unscored_replay_cases():
    cases = shadow_rows_to_cases(
        [
            {
                "intent_id": "i-1",
                "text": "Review this patch.",
                "legacy_classification": "code_review",
                "legacy_policy_action": "review",
                "policy_shadow_route": "task_graph",
                "policy_shadow_disposition": "create_tasks",
                "trajectory": {
                    "verifications": [
                        {
                            "source": "acceptance_tool",
                            "verified": True,
                        }
                    ]
                },
            }
        ]
    )

    assert cases[0]["case_id"] == "assistx-intent-i-1"
    assert cases[0]["task_family"] == "coding"
    assert cases[0]["acceptance"] == {}
    assert (
        cases[0]["baseline"]["recorded_verifications"][0]["verified"]
        is True
    )
