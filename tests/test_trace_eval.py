from assistx.evaluation.trace_eval import evaluate_trace


def _good_trace():
    return {
        "trace_id": "trace-good-1",
        "spans": [
            {"type": "task", "attributes": {"task_id": "t1"}},
            {"type": "route", "attributes": {"network_path": "tailscale"}},
            {"type": "model", "attributes": {"model": "local-test"}},
            {"type": "tool", "attributes": {"name": "repo_search"}},
        ],
        "outcome": {"status": "success", "evidence_ids": ["e1"]},
    }


def test_good_trace_passes_all_checks():
    result = evaluate_trace(_good_trace())
    assert result.passed is True
    assert result.score == 1.0
    assert not result.reasons


def test_public_route_is_rejected():
    trace = _good_trace()
    trace["spans"][1]["attributes"]["network_path"] = "public"
    result = evaluate_trace(trace)
    assert result.passed is False
    assert result.checks["no_public_route"] is False


def test_unbounded_retry_is_rejected():
    trace = _good_trace()
    trace["spans"].extend({"type": "retry"} for _ in range(3))
    result = evaluate_trace(trace)
    assert result.passed is False
    assert result.checks["no_unbounded_retry"] is False


def test_success_without_evidence_is_rejected():
    trace = _good_trace()
    trace["outcome"]["evidence_ids"] = []
    result = evaluate_trace(trace)
    assert result.passed is False
    assert result.checks["evidence_on_success"] is False


def test_missing_core_spans_is_rejected():
    result = evaluate_trace({"spans": [], "outcome": {"status": "failure"}})
    assert result.passed is False
    assert result.checks["has_task_span"] is False
    assert result.checks["has_route_span"] is False
    assert result.checks["has_model_span"] is False
