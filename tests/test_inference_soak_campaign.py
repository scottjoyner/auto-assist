from __future__ import annotations

from assistx.inference_policy_experiment import load_matrix
from assistx.inference_task_evaluator_report import (
    TASK_EVAL_REPORT_SCHEMA,
    load_task_evaluator_suite,
)
from assistx.inference_soak_campaign import (
    compile_campaign_plan,
    evaluate_campaign,
    evaluate_target_context,
    validate_campaign_config,
    validate_campaign_plan,
)


def _config():
    return validate_campaign_config(
        {
            "campaign_id": "test-32k-to-128k",
            "source_context_tokens": 32768,
            "target_context_tokens": 131072,
            "turns": 100,
            "nodes": ["x1-370", "r9700"],
            "speculations": ["none", "mtp", "dflash"],
            "require_fresh_process_pair": True,
            "require_same_runtime_revision": True,
            "require_same_launch_config": True,
        }
    )


def _matrix():
    return load_matrix(
        "examples/assistx-inference-policy-experiment/matrix.soak.json"
    )


def _plan():
    return compile_campaign_plan(
        _config(),
        _matrix(),
        profiles_file=(
            "examples/assistx-inference-policy-experiment/"
            "session-soak.profiles.json"
        ),
        matrix_file=(
            "examples/assistx-inference-policy-experiment/"
            "matrix.soak.json"
        ),
    )


QUALITY_SUITE = (
    "examples/assistx-inference-policy-experiment/"
    "task-evaluator.suite.json"
)


def _quality_config():
    return validate_campaign_config(
        {
            "campaign_id": "test-quality-32k-to-128k",
            "source_context_tokens": 32768,
            "target_context_tokens": 131072,
            "turns": 100,
            "nodes": ["x1-370", "r9700"],
            "speculations": ["none", "mtp", "dflash"],
            "require_fresh_process_pair": True,
            "require_same_runtime_revision": True,
            "require_same_launch_config": True,
            "require_task_quality_evidence": True,
        }
    )


def _quality_plan():
    return compile_campaign_plan(
        _quality_config(),
        _matrix(),
        profiles_file=(
            "examples/assistx-inference-policy-experiment/"
            "session-soak.profiles.json"
        ),
        matrix_file=(
            "examples/assistx-inference-policy-experiment/"
            "matrix.soak.json"
        ),
        task_quality_suite_file=QUALITY_SUITE,
    )


def _quality_report(plan, candidate, *, target=False, eligible=True):
    suite = load_task_evaluator_suite(QUALITY_SUITE)
    policy_id = (
        candidate["target_policy_id"]
        if target
        else candidate["source_policy_id"]
    )
    policy_sha256 = (
        candidate["target_policy_sha256"]
        if target
        else candidate["source_policy_sha256"]
    )
    return {
        "schema": TASK_EVAL_REPORT_SCHEMA,
        "suite_id": suite["suite_id"],
        "suite_sha256": suite["suite_sha256"],
        "cases_sha256": suite["cases_sha256"],
        "required_case_ids": suite["required_case_ids"],
        "policy_count": 1,
        "policies": [
            {
                "policy_id": policy_id,
                "policy_sha256": policy_sha256,
                "eligible_for_training_evidence": eligible,
            }
        ],
        "production_promotion_authorized": False,
        "routing_authority_changed": False,
        "authority": {
            "dispatch_allowed": False,
            "approval_granted": False,
            "claim_acquired": False,
            "mutation_allowed": False,
            "routing_authority_changed": False,
        },
        "report_sha256": "f" * 64,
    }


def _summary(
    candidate,
    mode,
    *,
    passed=True,
    process_started_at=1000,
    runtime_revision="llama.cpp@abc",
    launch_sha="a" * 64,
):
    return {
        "schema": "assistx-inference-session-soak-summary-v1",
        "session_id": (
            candidate["candidate_id"]
            + "-"
            + mode
        ),
        "profile_id": candidate["source_profile_id"],
        "profile_sha256": candidate["source_profile_sha256"],
        "policy_id": candidate["source_policy_id"],
        "policy_sha256": candidate["source_policy_sha256"],
        "mode": mode,
        "target_context_tokens": 32768,
        "expected_turns": 100,
        "completed_turns": 100,
        "passed": passed,
        "runtime_identity": {
            "consistent": True,
            "runtime_revision": runtime_revision,
            "launch_config_sha256": launch_sha,
            "process_started_at_unix_ms": process_started_at,
        },
        "rates": {
            "success": 1.0,
            "acceptance": 1.0,
            "canary_pass": 1.0,
            "telemetry_valid": 1.0,
        },
    }


def test_campaign_plan_pairs_all_32k_policies_to_128k():
    plan = _plan()

    assert plan["schema"] == "assistx-inference-soak-campaign-plan-v1"
    assert len(plan["candidates"]) == 6
    assert sum(
        len(candidate["runs"])
        for candidate in plan["candidates"]
    ) == 12
    assert {
        run["mode"]
        for candidate in plan["candidates"]
        for run in candidate["runs"]
    } == {"stable_prefix", "growing_prefix"}

    for candidate in plan["candidates"]:
        assert candidate["source_context_tokens"] == 32768
        assert candidate["target_context_tokens"] == 131072
        assert candidate["source_policy_id"] != candidate["target_policy_id"]


def test_campaign_plan_hash_rejects_tampering():
    plan = _plan()
    assert validate_campaign_plan(plan) is plan

    tampered = {
        **plan,
        "turns": 99,
    }
    try:
        validate_campaign_plan(tampered)
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("expected tampered campaign plan rejection")


def test_campaign_plan_authority_remains_all_false():
    plan = _plan()
    assert plan["routing_authority_changed"] is False
    assert all(
        value is False
        for value in plan["authority"].values()
    )


def test_campaign_advances_only_fresh_same_build_pair():
    plan = _plan()
    candidate = plan["candidates"][0]
    summaries = [
        _summary(
            candidate,
            "stable_prefix",
            process_started_at=1000,
        ),
        _summary(
            candidate,
            "growing_prefix",
            process_started_at=2000,
        ),
    ]

    evidence = evaluate_campaign(plan, summaries)

    first = next(
        row
        for row in evidence["evaluated_candidates"]
        if row["candidate_id"] == candidate["candidate_id"]
    )
    assert first["eligible_for_target_context_experiment"] is True
    assert first["checks"]["fresh_process_pair"]["passed"] is True
    assert first["checks"]["same_runtime_revision"]["passed"] is True
    assert first["checks"]["same_launch_config"]["passed"] is True
    assert evidence["eligible_target_context_policies"] == [
        {
            "candidate_id": candidate["candidate_id"],
            "target_policy_id": candidate["target_policy_id"],
            "target_policy_sha256": candidate["target_policy_sha256"],
            "target_profile_id": candidate["target_profile_id"],
            "target_profile_sha256": candidate["target_profile_sha256"],
            "target_context_tokens": 131072,
        }
    ]
    assert evidence["production_promotion_authorized"] is False
    assert evidence["routing_authority_changed"] is False


def test_quality_campaign_plan_binds_exact_suite_and_cases():
    plan = _quality_plan()
    suite = load_task_evaluator_suite(QUALITY_SUITE)

    assert plan["requirements"]["task_quality_evidence"] is True
    assert plan["task_quality_suite_id"] == suite["suite_id"]
    assert plan["task_quality_suite_sha256"] == suite["suite_sha256"]
    assert plan["task_quality_cases_sha256"] == suite["cases_sha256"]


def test_quality_campaign_requires_report_before_128k_advancement():
    plan = _quality_plan()
    candidate = plan["candidates"][0]
    summaries = [
        _summary(candidate, "stable_prefix", process_started_at=1000),
        _summary(candidate, "growing_prefix", process_started_at=2000),
    ]

    try:
        evaluate_campaign(plan, summaries)
    except ValueError as exc:
        assert "task quality evidence is required" in str(exc)
    else:
        raise AssertionError("expected missing task-quality evidence rejection")


def test_quality_campaign_advances_only_when_quality_report_passes():
    plan = _quality_plan()
    candidate = plan["candidates"][0]
    summaries = [
        _summary(candidate, "stable_prefix", process_started_at=1000),
        _summary(candidate, "growing_prefix", process_started_at=2000),
    ]
    quality = _quality_report(plan, candidate)

    evidence = evaluate_campaign(
        plan,
        summaries,
        task_quality_report=quality,
    )
    row = next(
        item
        for item in evidence["evaluated_candidates"]
        if item["candidate_id"] == candidate["candidate_id"]
    )

    assert row["checks"]["task_quality_evidence"]["passed"] is True
    assert row["eligible_for_target_context_experiment"] is True


def test_quality_campaign_blocks_failed_quality_even_with_green_soak():
    plan = _quality_plan()
    candidate = plan["candidates"][0]
    summaries = [
        _summary(candidate, "stable_prefix", process_started_at=1000),
        _summary(candidate, "growing_prefix", process_started_at=2000),
    ]
    quality = _quality_report(
        plan,
        candidate,
        eligible=False,
    )

    evidence = evaluate_campaign(
        plan,
        summaries,
        task_quality_report=quality,
    )
    row = next(
        item
        for item in evidence["evaluated_candidates"]
        if item["candidate_id"] == candidate["candidate_id"]
    )

    assert row["checks"]["task_quality_evidence"]["passed"] is False
    assert row["eligible_for_target_context_experiment"] is False


def test_campaign_rejects_reused_process_between_modes():
    plan = _plan()
    candidate = plan["candidates"][0]
    summaries = [
        _summary(
            candidate,
            "stable_prefix",
            process_started_at=1000,
        ),
        _summary(
            candidate,
            "growing_prefix",
            process_started_at=1000,
        ),
    ]

    evidence = evaluate_campaign(plan, summaries)
    row = next(
        item
        for item in evidence["evaluated_candidates"]
        if item["candidate_id"] == candidate["candidate_id"]
    )

    assert row["eligible_for_target_context_experiment"] is False
    assert row["checks"]["fresh_process_pair"]["passed"] is False


def test_campaign_rejects_different_revision_or_launch_config():
    plan = _plan()
    candidate = plan["candidates"][0]
    summaries = [
        _summary(
            candidate,
            "stable_prefix",
            process_started_at=1000,
            runtime_revision="llama.cpp@abc",
            launch_sha="a" * 64,
        ),
        _summary(
            candidate,
            "growing_prefix",
            process_started_at=2000,
            runtime_revision="llama.cpp@def",
            launch_sha="b" * 64,
        ),
    ]

    evidence = evaluate_campaign(plan, summaries)
    row = next(
        item
        for item in evidence["evaluated_candidates"]
        if item["candidate_id"] == candidate["candidate_id"]
    )

    assert row["eligible_for_target_context_experiment"] is False
    assert row["checks"]["same_runtime_revision"]["passed"] is False
    assert row["checks"]["same_launch_config"]["passed"] is False


def test_campaign_rejects_profile_hash_drift():
    plan = _plan()
    candidate = plan["candidates"][0]
    stable = _summary(
        candidate,
        "stable_prefix",
        process_started_at=1000,
    )
    growing = _summary(
        candidate,
        "growing_prefix",
        process_started_at=2000,
    )
    growing["profile_sha256"] = "f" * 64

    evidence = evaluate_campaign(plan, [stable, growing])
    row = next(
        item
        for item in evidence["evaluated_candidates"]
        if item["candidate_id"] == candidate["candidate_id"]
    )

    assert row["eligible_for_target_context_experiment"] is False
    assert row["checks"]["growing_profile_sha256"]["passed"] is False


def test_campaign_rejects_failed_or_incomplete_summary():
    plan = _plan()
    candidate = plan["candidates"][0]
    stable = _summary(
        candidate,
        "stable_prefix",
        process_started_at=1000,
    )
    growing = _summary(
        candidate,
        "growing_prefix",
        passed=False,
        process_started_at=2000,
    )
    growing["completed_turns"] = 99

    evidence = evaluate_campaign(plan, [stable, growing])
    row = next(
        item
        for item in evidence["evaluated_candidates"]
        if item["candidate_id"] == candidate["candidate_id"]
    )

    assert row["eligible_for_target_context_experiment"] is False
    assert row["checks"]["growing_passed"]["passed"] is False
    assert row["checks"]["growing_turns_complete"]["passed"] is False


def test_campaign_rejects_duplicate_summary_for_same_policy_mode():
    plan = _plan()
    candidate = plan["candidates"][0]
    stable = _summary(
        candidate,
        "stable_prefix",
        process_started_at=1000,
    )
    growing = _summary(
        candidate,
        "growing_prefix",
        process_started_at=2000,
    )

    try:
        evaluate_campaign(
            plan,
            [stable, stable, growing],
        )
    except ValueError as exc:
        assert "duplicate soak summary" in str(exc)
    else:
        raise AssertionError("expected duplicate summary rejection")


def _target_summary(
    candidate,
    mode,
    *,
    passed=True,
    process_started_at=3000,
    runtime_revision="llama.cpp@abc",
    launch_sha="c" * 64,
):
    return {
        "schema": "assistx-inference-session-soak-summary-v1",
        "session_id": (
            candidate["candidate_id"]
            + "-target-"
            + mode
        ),
        "profile_id": candidate["target_profile_id"],
        "profile_sha256": candidate["target_profile_sha256"],
        "policy_id": candidate["target_policy_id"],
        "policy_sha256": candidate["target_policy_sha256"],
        "mode": mode,
        "target_context_tokens": 131072,
        "expected_turns": 100,
        "completed_turns": 100,
        "passed": passed,
        "runtime_identity": {
            "consistent": True,
            "runtime_revision": runtime_revision,
            "launch_config_sha256": launch_sha,
            "process_started_at_unix_ms": process_started_at,
        },
        "rates": {
            "success": 1.0,
            "acceptance": 1.0,
            "canary_pass": 1.0,
            "telemetry_valid": 1.0,
        },
    }


def _source_evidence_for_candidate(plan, candidate):
    return evaluate_campaign(
        plan,
        [
            _summary(
                candidate,
                "stable_prefix",
                process_started_at=1000,
            ),
            _summary(
                candidate,
                "growing_prefix",
                process_started_at=2000,
            ),
        ],
    )


def test_target_context_gate_completes_fresh_128k_pair():
    plan = _plan()
    candidate = plan["candidates"][0]
    source_evidence = _source_evidence_for_candidate(
        plan,
        candidate,
    )

    target_evidence = evaluate_target_context(
        plan,
        source_evidence,
        [
            _target_summary(
                candidate,
                "stable_prefix",
                process_started_at=3000,
            ),
            _target_summary(
                candidate,
                "growing_prefix",
                process_started_at=4000,
            ),
        ],
    )

    row = target_evidence["evaluated_target_candidates"][0]
    assert row["benchmark_complete_at_target_context"] is True
    assert row["checks"]["fresh_process_pair"]["passed"] is True
    assert (
        target_evidence[
            "benchmark_complete_target_context_policies"
        ][0]["context_tokens"]
        == 131072
    )
    assert target_evidence["production_promotion_authorized"] is False
    assert target_evidence["routing_authority_changed"] is False


def test_quality_campaign_requires_quality_again_at_128k():
    plan = _quality_plan()
    candidate = plan["candidates"][0]
    source_quality = _quality_report(plan, candidate)
    source_evidence = evaluate_campaign(
        plan,
        [
            _summary(candidate, "stable_prefix", process_started_at=1000),
            _summary(candidate, "growing_prefix", process_started_at=2000),
        ],
        task_quality_report=source_quality,
    )
    target_summaries = [
        _target_summary(
            candidate,
            "stable_prefix",
            process_started_at=3000,
        ),
        _target_summary(
            candidate,
            "growing_prefix",
            process_started_at=4000,
        ),
    ]

    try:
        evaluate_target_context(
            plan,
            source_evidence,
            target_summaries,
        )
    except ValueError as exc:
        assert "task quality evidence is required" in str(exc)
    else:
        raise AssertionError(
            "expected missing target task-quality evidence rejection"
        )

    target_quality = _quality_report(
        plan,
        candidate,
        target=True,
    )
    target_evidence = evaluate_target_context(
        plan,
        source_evidence,
        target_summaries,
        task_quality_report=target_quality,
    )
    row = target_evidence["evaluated_target_candidates"][0]
    assert row["checks"]["task_quality_evidence"]["passed"] is True
    assert row["benchmark_complete_at_target_context"] is True


def test_target_context_gate_rejects_reused_128k_process():
    plan = _plan()
    candidate = plan["candidates"][0]
    source_evidence = _source_evidence_for_candidate(
        plan,
        candidate,
    )

    target_evidence = evaluate_target_context(
        plan,
        source_evidence,
        [
            _target_summary(
                candidate,
                "stable_prefix",
                process_started_at=3000,
            ),
            _target_summary(
                candidate,
                "growing_prefix",
                process_started_at=3000,
            ),
        ],
    )

    row = target_evidence["evaluated_target_candidates"][0]
    assert row["benchmark_complete_at_target_context"] is False
    assert row["checks"]["fresh_process_pair"]["passed"] is False


def test_target_context_gate_rejects_source_evidence_plan_drift():
    plan = _plan()
    candidate = plan["candidates"][0]
    source_evidence = _source_evidence_for_candidate(
        plan,
        candidate,
    )
    source_evidence["plan_sha256"] = "0" * 64

    try:
        evaluate_target_context(
            plan,
            source_evidence,
            [
                _target_summary(
                    candidate,
                    "stable_prefix",
                    process_started_at=3000,
                ),
                _target_summary(
                    candidate,
                    "growing_prefix",
                    process_started_at=4000,
                ),
            ],
        )
    except ValueError as exc:
        assert "plan hash mismatch" in str(exc)
    else:
        raise AssertionError(
            "expected target-context source-evidence rejection"
        )


def test_campaign_missing_pair_never_advances():
    plan = _plan()
    candidate = plan["candidates"][0]

    evidence = evaluate_campaign(
        plan,
        [
            _summary(
                candidate,
                "stable_prefix",
                process_started_at=1000,
            )
        ],
    )
    row = next(
        item
        for item in evidence["evaluated_candidates"]
        if item["candidate_id"] == candidate["candidate_id"]
    )

    assert row["eligible_for_target_context_experiment"] is False
    assert row["checks"]["stable_present"]["passed"] is True
    assert row["checks"]["growing_present"]["passed"] is False
