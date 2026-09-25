from __future__ import annotations

from assistx.inference_policy_experiment import load_matrix
from assistx.inference_soak_campaign import (
    compile_campaign_plan,
    evaluate_campaign,
    validate_campaign_config,
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
