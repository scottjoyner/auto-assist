from assistx.evaluation.experiment_results import compare_variants
from assistx.evaluation.promotion import PromotionThresholds, evaluate_promotion
from assistx.evaluation.trace_recorder import benchmark_trace


def _trace(task_id: str, *, success: bool, duration_ms: float):
    trace = benchmark_trace(
        task_id=task_id,
        node_id="x1-370",
        model="qwen-test",
        task_family="coding",
        success=success,
        validation_passed=success,
    )
    trace["outcome"]["duration_ms"] = duration_ms
    return trace


def test_candidate_with_enough_runs_and_no_regressions_is_eligible():
    baseline = [_trace(f"b{i}", success=True, duration_ms=200) for i in range(5)]
    candidate = [_trace(f"c{i}", success=True, duration_ms=150) for i in range(5)]
    comparison = compare_variants("baseline", baseline, "candidate", candidate)
    decision = evaluate_promotion(
        comparison,
        thresholds=PromotionThresholds(min_runs=5, max_mean_duration_ms_delta=0),
        safety_regressions=0,
        fault_gates_passed=True,
    )
    assert decision.eligible is True
    assert not decision.reasons


def test_single_lucky_run_cannot_promote():
    comparison = compare_variants(
        "baseline", [_trace("b", success=False, duration_ms=300)],
        "candidate", [_trace("c", success=True, duration_ms=100)],
    )
    decision = evaluate_promotion(
        comparison,
        thresholds=PromotionThresholds(min_runs=5),
        fault_gates_passed=True,
    )
    assert decision.eligible is False
    assert "insufficient_runs" in decision.reasons


def test_safety_or_missing_fault_gate_blocks_promotion():
    traces = [_trace(str(i), success=True, duration_ms=100) for i in range(5)]
    comparison = compare_variants("baseline", traces, "candidate", traces)
    decision = evaluate_promotion(
        comparison,
        thresholds=PromotionThresholds(min_runs=5),
        safety_regressions=1,
        fault_gates_passed=False,
    )
    assert decision.eligible is False
    assert "safety_regression" in decision.reasons
    assert "fault_gates_not_passed" in decision.reasons
