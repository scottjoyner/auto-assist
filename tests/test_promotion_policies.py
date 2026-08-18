import pytest

from assistx.evaluation.promotion_policies import (
    evaluate_operational_metrics,
    get_promotion_policy,
)


def test_context_compression_policy_requires_real_savings_and_zero_fidelity_failures():
    policy = get_promotion_policy("context-compression")
    passed, failures = evaluate_operational_metrics(
        policy,
        {
            "input_token_reduction_ratio": 0.20,
            "fidelity_failure_rate": 0.0,
        },
    )
    assert passed is True
    assert failures == ()


def test_context_compression_policy_rejects_weak_savings():
    policy = get_promotion_policy("context-compression")
    passed, failures = evaluate_operational_metrics(
        policy,
        {
            "input_token_reduction_ratio": 0.10,
            "fidelity_failure_rate": 0.0,
        },
    )
    assert passed is False
    assert "threshold:input_token_reduction_ratio" in failures


def test_cache_affinity_policy_rejects_any_routing_safety_regression():
    policy = get_promotion_policy("cache-affinity")
    passed, failures = evaluate_operational_metrics(
        policy,
        {
            "median_ttft_reduction_ratio": 0.15,
            "routing_safety_regressions": 1,
        },
    )
    assert passed is False
    assert "threshold:routing_safety_regressions" in failures


def test_unknown_policy_is_rejected():
    with pytest.raises(ValueError, match="unknown experiment"):
        get_promotion_policy("unknown")
