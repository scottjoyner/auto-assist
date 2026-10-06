from src.assistx.model_fitness import (
    SubagentTraceRecord,
    aggregate_by_model_task,
    aggregate_fitness,
    classify_outcome,
    compute_fitness_record,
)


def rec(
    *,
    provider="kilo_free",
    model="nvidia/nemotron-3-super-120b-a12b:free",
    status="completed",
    family="coding",
    failure=None,
    patches=0,
    launch="a",
    final="a",
    tools=None,
    tokens=None,
    identity=True,
    route_kind="exact",
    tests_passed=None,
):
    return SubagentTraceRecord(
        provider=provider,
        requested_model=model,
        effective_model=model,
        model_identity_complete=identity,
        route_kind=route_kind,
        terminal_status=status,
        task_family=family,
        failure_class=failure,
        assistant_error_count=0,
        tool_counts=tools or {},
        patch_parts=patches,
        launch_head=launch,
        final_git_head=final,
        dirty_path_evidence=False,
        token_counts=tokens or {"input": 100, "output": 20},
        tests_passed=tests_passed,
    )


def test_provider_failures_do_not_count_as_model_quality_failures():
    row = rec(status="failed", failure="provider_overloaded")
    assert classify_outcome(row) == "provider_failure"
    result = compute_fitness_record(row)
    assert result["provider_failure"] is True
    assert result["model_failed"] is False

    agg = aggregate_fitness([row])
    assert agg["provider_failures"] == 1
    assert agg["quality_scored_runs"] == 0
    assert agg["model_failures"] == 0


def test_stalled_healthy_provider_is_no_progress_model_signal():
    row = rec(status="stalled_abandoned", tools={"read": 8})
    assert classify_outcome(row) == "no_progress"
    result = compute_fitness_record(row)
    assert result["terminal_status"] == "stalled_abandoned"
    assert result["model_failed"] is True
    assert result["no_progress"] is True


def test_productive_exact_coding_run_scores_high():
    row = rec(
        patches=3,
        final="b",
        tools={"read": 4, "edit": 3, "bash": 2},
        tests_passed=True,
    )
    agg = aggregate_fitness([row])
    assert agg["successful_runs"] == 1
    assert agg["patch_producing_runs"] == 1
    assert agg["test_evidence_runs"] == 1
    assert agg["passing_test_runs"] == 1
    assert agg["fitness_score"] >= 90


def test_router_alias_and_incomplete_identity_are_excluded():
    alias = rec(route_kind="alias")
    incomplete = rec(identity=False)
    agg = aggregate_fitness([alias, incomplete])
    assert agg["excluded_runs"] == 2
    assert agg["quality_scored_runs"] == 0


def test_dots_canary_does_not_erase_dots_coding_no_progress():
    model = "dots-studio/dots-3-note-preview:free"
    records = [
        rec(model=model, family="canary", status="completed", tools={"bash": 1}),
        rec(model=model, family="coding", status="stalled_abandoned", tools={"read": 6}),
    ]
    grouped = aggregate_by_model_task(records)
    canary = grouped[("kilo_free", model, "canary")]
    coding = grouped[("kilo_free", model, "coding")]
    assert canary["success_rate"] == 1.0
    assert coding["no_progress_rate"] == 1.0
    assert coding["fitness_score"] < canary["fitness_score"]


def test_nemotron_provider_overload_does_not_reduce_coding_fitness():
    model = "nvidia/nemotron-3-ultra-550b-a55b:free"
    records = [
        rec(model=model, status="failed", failure="provider_overloaded"),
        rec(model=model, patches=1, final="b", tools={"edit": 1}, tests_passed=True),
    ]
    agg = aggregate_fitness(records)
    assert agg["provider_failures"] == 1
    assert agg["quality_scored_runs"] == 1
    assert agg["success_rate"] == 1.0
    assert agg["fitness_score"] >= 90


def test_token_proxy_reports_efficiency_without_rewarding_more_tokens():
    low = rec(tokens={"input": 100, "output": 20, "reasoning": 10, "cache_read": 50})
    high = rec(tokens={"input": 1000, "output": 200, "reasoning": 100, "cache_read": 500})
    low_agg = aggregate_fitness([low])
    high_agg = aggregate_fitness([high])
    assert low_agg["mean_token_proxy"] < high_agg["mean_token_proxy"]
    assert low_agg["fitness_score"] == high_agg["fitness_score"]


def test_grouping_is_exact_provider_model_task_family():
    rows = [
        rec(model="cohere/north-mini-code:free", family="coding"),
        rec(model="cohere/north-mini-code:free", family="review"),
        rec(model="liquid/lfm-2.5-2.6b:free", family="coding"),
    ]
    grouped = aggregate_by_model_task(rows)
    assert len(grouped) == 3
    assert (
        "kilo_free",
        "cohere/north-mini-code:free",
        "coding",
    ) in grouped
