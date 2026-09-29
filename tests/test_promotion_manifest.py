from assistx.evaluation.experiment_results import compare_variants
from assistx.evaluation.promotion import PromotionThresholds, evaluate_promotion
from assistx.evaluation.promotion_manifest import build_canary_promotion_manifest
from assistx.evaluation.promotion_policies import get_promotion_policy
from assistx.evaluation.trace_recorder import benchmark_trace


def _trace(task_id: str, duration_ms: float):
    trace = benchmark_trace(
        task_id=task_id,
        node_id="x1-370",
        model="qwen-test",
        task_family="coding",
        success=True,
        validation_passed=True,
    )
    trace["outcome"]["duration_ms"] = duration_ms
    return trace


def _statistical_decision():
    comparison = compare_variants(
        "baseline", [_trace(f"b{i}", 200) for i in range(10)],
        "candidate", [_trace(f"c{i}", 150) for i in range(10)],
    )
    return evaluate_promotion(
        comparison,
        thresholds=PromotionThresholds(min_runs=10, max_mean_duration_ms_delta=0),
        fault_gates_passed=True,
    )


def test_operator_approval_remains_required_after_all_technical_gates():
    policy = get_promotion_policy("cache-affinity")
    manifest = build_canary_promotion_manifest(
        experiment_manifest_id="manifest-1",
        policy=policy,
        statistical_decision=_statistical_decision(),
        operational_metrics={
            "median_ttft_reduction_ratio": 0.15,
            "routing_safety_regressions": 0,
        },
        passed_fault_gates=set(policy.required_fault_gates),
        operator_approved=False,
    )
    assert manifest.canary_eligible is False
    assert manifest.blockers == ("operator_approval_required",)


def test_all_gates_plus_operator_approval_make_canary_eligible():
    policy = get_promotion_policy("cache-affinity")
    manifest = build_canary_promotion_manifest(
        experiment_manifest_id="manifest-1",
        policy=policy,
        statistical_decision=_statistical_decision(),
        operational_metrics={
            "median_ttft_reduction_ratio": 0.15,
            "routing_safety_regressions": 0,
        },
        passed_fault_gates=set(policy.required_fault_gates),
        operator_approved=True,
    )
    assert manifest.canary_eligible is True
    assert manifest.blockers == ()


def test_missing_fault_gate_blocks_canary():
    policy = get_promotion_policy("context-compression")
    manifest = build_canary_promotion_manifest(
        experiment_manifest_id="manifest-2",
        policy=policy,
        statistical_decision=_statistical_decision(),
        operational_metrics={
            "input_token_reduction_ratio": 0.20,
            "fidelity_failure_rate": 0.0,
        },
        passed_fault_gates={"exact_value_fidelity"},
        operator_approved=True,
    )
    assert manifest.canary_eligible is False
    assert "fault:whitespace_sensitive_identity" in manifest.blockers
