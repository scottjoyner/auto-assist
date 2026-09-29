from assistx.evaluation.experiment_results import compare_variants, summarize_variant
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


def test_variant_summary_reports_pass_rate_score_and_duration():
    summary = summarize_variant("baseline", [_trace("a", success=True, duration_ms=100), _trace("b", success=False, duration_ms=200)])
    assert summary.runs == 2
    assert summary.pass_rate == 0.5
    assert 0 < summary.mean_score < 1
    assert summary.mean_duration_ms == 150


def test_compare_variants_reports_candidate_deltas():
    baseline = [_trace("a", success=False, duration_ms=200)]
    candidate = [_trace("b", success=True, duration_ms=120)]
    comparison = compare_variants("raw", baseline, "optimized", candidate)
    assert comparison["pass_rate_delta"] == 1.0
    assert comparison["score_delta"] > 0
    assert comparison["mean_duration_ms_delta"] == -80
