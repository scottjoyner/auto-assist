from __future__ import annotations

from assistx.inference_session_soak import (
    append_history,
    compare_soak_summaries,
    build_seed_context,
    build_turn_case,
    check_session_runtime_identity,
    compile_soak_plan,
    finalize_pending_commit,
    initial_state,
    is_canary_turn,
    messages_for_turn,
    stage_pending_commit,
    summarize_soak_results,
    validate_resume_state,
    validate_soak_profile,
    verify_pending_result_row,
)


def _profile(**overrides):
    value = {
        "profile_id": "soak-32k",
        "target_context_tokens": 32768,
        "default_turns": 100,
        "min_turns": 20,
        "max_turns": 500,
        "canary_every": 10,
        "retention_marker": "ASSISTX-SOAK-7391",
        "prefix_chars_per_token": 4.2,
        "stable_prefix_fraction": 0.9,
        "growing_prefix_fraction": 0.7,
        "thresholds": {
            "min_success_rate": 0.99,
            "min_acceptance_rate": 0.98,
            "min_canary_pass_rate": 1.0,
            "min_telemetry_valid_rate": 1.0,
            "max_ttft_drift_ratio": 2.0,
            "max_wall_drift_ratio": 2.0,
            "max_vram_growth_bytes": 1024,
        },
    }
    value.update(overrides)
    return validate_soak_profile(value)


def _policy(**overrides):
    value = {
        "policy_id": "r9700-q4-dflash-32k",
        "policy_sha256": "b" * 64,
        "node_id": "r9700",
        "model_handle": "qwen3.8-27b-q4",
        "backend": "rocm",
        "quantization": "q4",
        "speculation": "dflash",
        "context_tokens": 32768,
        "concurrency": 1,
        "telemetry_required": True,
    }
    value.update(overrides)
    return value


def _telemetry(
    *,
    process_started_at=1000,
    vram=100,
    cache_reuse=0.8,
    spec_acceptance=0.75,
):
    return {
        "valid": True,
        "runtime_revision": "llama.cpp@abc",
        "launch_config_sha256": "a" * 64,
        "process_started_at_unix_ms": process_started_at,
        "gauge_samples": {
            "vram_bytes": {
                "before": vram - 1,
                "after": vram,
            }
        },
        "derived": {
            "cache_reuse_rate": cache_reuse,
            "spec_acceptance_rate": spec_acceptance,
        },
    }


def _row(
    turn,
    *,
    canary=False,
    ttft=100.0,
    wall=300.0,
    prompt_tokens=30000,
    vram=100,
    accepted=True,
    telemetry_valid=True,
):
    return {
        "turn_index": turn,
        "turn_class": (
            "coding",
            "tool_json",
            "reasoning",
            "prose",
        )[(turn - 1) % 4],
        "canary": canary,
        "success": True,
        "acceptance_passed": accepted,
        "telemetry_valid": telemetry_valid,
        "ttft_ms": ttft,
        "wall_ms": wall,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": 20,
        "runtime_telemetry": _telemetry(vram=vram),
    }


def test_plan_rejects_policy_smaller_than_context_target():
    profile = _profile()
    policy = _policy(context_tokens=8192)

    try:
        compile_soak_plan(
            profile,
            policy,
            mode="stable_prefix",
        )
    except ValueError as exc:
        assert "smaller" in str(exc)
    else:
        raise AssertionError("expected context-size rejection")


def test_seed_contains_retention_and_authority_canaries():
    profile = _profile()
    seed = build_seed_context(
        profile,
        mode="stable_prefix",
    )

    assert profile["retention_marker"] in seed
    assert "ADVISORY-ONLY" in seed
    assert "mutation_allowed=false" in seed
    assert len(seed) > 100_000


def test_canary_schedule_includes_first_periodic_and_last():
    profile = _profile(canary_every=10)

    assert is_canary_turn(profile, 1, 25)
    assert is_canary_turn(profile, 10, 25)
    assert is_canary_turn(profile, 20, 25)
    assert is_canary_turn(profile, 25, 25)
    assert not is_canary_turn(profile, 9, 25)


def test_turn_case_requires_marker_on_canary():
    profile = _profile()
    plan = compile_soak_plan(
        profile,
        _policy(),
        mode="stable_prefix",
        turns=20,
    )
    messages = [
        {
            "role": "system",
            "content": build_seed_context(
                profile,
                mode="stable_prefix",
            ),
        }
    ]

    case = build_turn_case(
        profile,
        session_id=plan["session_id"],
        turn_index=10,
        total_turns=20,
        messages=messages,
    )

    required = case["acceptance"]["required_terms"]
    assert "TURN-0010-OK" in required
    assert profile["retention_marker"] in required
    assert "ADVISORY-ONLY" in required


def test_tool_json_turn_keeps_marker_inside_structured_request():
    profile = _profile()
    plan = compile_soak_plan(
        profile,
        _policy(),
        mode="stable_prefix",
        turns=20,
    )
    messages = messages_for_turn(
        profile,
        initial_state(plan),
    )
    case = build_turn_case(
        profile,
        session_id=plan["session_id"],
        turn_index=2,
        total_turns=20,
        messages=messages,
    )

    prompt = case["messages"][-1]["content"]
    assert "Return exactly one compact JSON object" in prompt
    assert 'turn_marker="TURN-0002-OK"' in prompt
    assert "End the response with TURN-0002-OK" not in prompt


def test_growing_prefix_checkpoint_reconstructs_conversation():
    profile = _profile()
    plan = compile_soak_plan(
        profile,
        _policy(),
        mode="growing_prefix",
        turns=20,
    )
    state = initial_state(plan)
    messages = messages_for_turn(profile, state)
    case = build_turn_case(
        profile,
        session_id=plan["session_id"],
        turn_index=1,
        total_turns=20,
        messages=messages,
    )

    append_history(
        state,
        case,
        "synthetic assistant response TURN-0001-OK",
    )
    rebuilt = messages_for_turn(profile, state)

    assert len(rebuilt) == 3
    assert rebuilt[-2]["role"] == "user"
    assert rebuilt[-1]["role"] == "assistant"
    assert "TURN-0001-OK" in rebuilt[-1]["content"]


def test_stable_prefix_does_not_store_conversation_history():
    profile = _profile()
    plan = compile_soak_plan(
        profile,
        _policy(),
        mode="stable_prefix",
        turns=20,
    )
    state = initial_state(plan)
    messages = messages_for_turn(profile, state)
    case = build_turn_case(
        profile,
        session_id=plan["session_id"],
        turn_index=1,
        total_turns=20,
        messages=messages,
    )

    append_history(state, case, "response")

    assert state["history"] == []
    assert len(messages_for_turn(profile, state)) == 1


def test_resume_state_is_exact_hash_bound():
    profile = _profile()
    plan = compile_soak_plan(
        profile,
        _policy(),
        mode="stable_prefix",
        turns=20,
    )
    state = initial_state(plan)

    assert validate_resume_state(state, plan) is state

    changed = {**plan, "policy_sha256": "c" * 64}
    try:
        validate_resume_state(state, changed)
    except ValueError as exc:
        assert "policy_sha256" in str(exc)
    else:
        raise AssertionError("expected exact-policy resume rejection")


def test_pending_commit_recovers_append_checkpoint_crash_window():
    profile = _profile()
    plan = compile_soak_plan(
        profile,
        _policy(),
        mode="growing_prefix",
        turns=20,
    )
    state = initial_state(plan)
    result = {
        "session_id": plan["session_id"],
        "turn_index": 1,
        "success": True,
    }
    user = {
        "role": "user",
        "content": "synthetic turn one",
    }

    stage_pending_commit(
        state,
        result=result,
        user_message=user,
        assistant_output="assistant one",
    )

    assert state["completed_turns"] == 0
    assert state["pending_commit"] is not None
    verify_pending_result_row(state, result)
    finalize_pending_commit(state)

    assert state["completed_turns"] == 1
    assert state["pending_commit"] is None
    assert state["history"] == [
        user,
        {
            "role": "assistant",
            "content": "assistant one",
        },
    ]


def test_pending_commit_detects_mismatched_appended_result():
    profile = _profile()
    plan = compile_soak_plan(
        profile,
        _policy(),
        mode="stable_prefix",
        turns=20,
    )
    state = initial_state(plan)
    result = {
        "session_id": plan["session_id"],
        "turn_index": 1,
        "success": True,
    }
    stage_pending_commit(
        state,
        result=result,
        user_message={
            "role": "user",
            "content": "turn one",
        },
        assistant_output="response",
    )

    bad = {**result, "success": False}
    try:
        verify_pending_result_row(state, bad)
    except ValueError as exc:
        assert "hash" in str(exc)
    else:
        raise AssertionError("expected pending-row hash rejection")


def test_session_runtime_identity_rejects_restart_between_turns():
    profile = _profile()
    plan = compile_soak_plan(
        profile,
        _policy(),
        mode="stable_prefix",
        turns=20,
    )
    state = initial_state(plan)

    first_ok, first_error = check_session_runtime_identity(
        state,
        {"runtime_telemetry": _telemetry(process_started_at=1000)},
    )
    second_ok, second_error = check_session_runtime_identity(
        state,
        {"runtime_telemetry": _telemetry(process_started_at=2000)},
    )

    assert first_ok is True
    assert first_error is None
    assert second_ok is False
    assert "changed" in str(second_error)


def test_summary_passes_stable_session_inside_drift_and_memory_limits():
    profile = _profile()
    plan = compile_soak_plan(
        profile,
        _policy(),
        mode="stable_prefix",
        turns=20,
    )
    rows = [
        _row(
            turn,
            canary=is_canary_turn(profile, turn, 20),
            ttft=100 + turn,
            wall=300 + turn,
            prompt_tokens=30000,
            vram=100 + turn,
        )
        for turn in range(1, 21)
    ]

    summary = summarize_soak_results(
        profile,
        plan,
        rows,
    )

    assert summary["passed"] is True
    assert summary["rates"]["canary_pass"] == 1.0
    assert summary["context"]["prompt_tokens_first"] == 30000
    assert summary["memory"]["vram_growth_bytes"] == 19
    assert summary["cache_and_speculation"]["spec_acceptance_mean"] == 0.75


def test_summary_fails_latency_drift_and_canary_loss():
    profile = _profile()
    plan = compile_soak_plan(
        profile,
        _policy(),
        mode="growing_prefix",
        turns=20,
    )
    rows = []
    for turn in range(1, 21):
        canary = is_canary_turn(profile, turn, 20)
        rows.append(
            _row(
                turn,
                canary=canary,
                ttft=100 if turn < 19 else 400,
                wall=300 if turn < 19 else 1000,
                accepted=not (turn == 20),
            )
        )

    summary = summarize_soak_results(
        profile,
        plan,
        rows,
    )

    assert summary["passed"] is False
    assert summary["gates"]["canary_pass_rate"]["passed"] is False
    assert summary["gates"]["ttft_drift_ratio"]["passed"] is False

def test_compare_soak_summaries_pairs_stable_and_growing_modes():
    profile = _profile()
    stable_plan = compile_soak_plan(
        profile,
        _policy(),
        mode="stable_prefix",
        turns=20,
    )
    growing_plan = compile_soak_plan(
        profile,
        _policy(),
        mode="growing_prefix",
        turns=20,
    )
    stable_rows = [
        _row(
            turn,
            canary=is_canary_turn(profile, turn, 20),
            ttft=100,
            wall=300,
            vram=100 + turn,
        )
        for turn in range(1, 21)
    ]
    growing_rows = [
        _row(
            turn,
            canary=is_canary_turn(profile, turn, 20),
            ttft=125,
            wall=330,
            vram=100 + (turn * 2),
        )
        for turn in range(1, 21)
    ]

    stable = summarize_soak_results(
        profile,
        stable_plan,
        stable_rows,
    )
    growing = summarize_soak_results(
        profile,
        growing_plan,
        growing_rows,
    )
    report = compare_soak_summaries([stable, growing])

    assert len(report["stable_growing_pairs"]) == 1
    pair = report["stable_growing_pairs"][0]
    assert pair["growing_vs_stable"]["ttft_p50_ratio"] == 1.25
    assert pair["growing_vs_stable"]["wall_p50_ratio"] == 1.1
    assert report["routing_authority_changed"] is False
