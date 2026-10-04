import pytest

from assistx.fleet_latency_regret import build_latency_regret_evidence


AUTH_ARTIFACT = "a" * 64
SHADOW_ARTIFACT = "b" * 64
AUTH_HANDLE = f"deathstar/ling/llama.cpp@ling/{AUTH_ARTIFACT}"


def _plan() -> dict:
    return {
        "schema": "assistx-fleet-latency-shadow-plan-v1",
        "origin_node_id": "x1-370",
        "task_family": "decision_judge",
        "recommended": {
            "node_id": "xwing",
            "model_id": "k2",
            "runtime_id": "llama.cpp@k2",
            "model_artifact_sha256": SHADOW_ARTIFACT,
            "estimated_time_to_useful_result_ms": 220.0,
        },
        "alternatives": [],
        "rejected": [],
        "executable": False,
        "authority": {
            "routing_authority_changed": False,
            "admission_changed": False,
            "dispatch_allowed": False,
            "approval_granted": False,
            "claim_acquired": False,
            "mutation_allowed": False,
        },
    }


def _outcome(
    *,
    node: str,
    model: str,
    runtime: str,
    passed: bool,
    completion_ms: float,
    memory: int | None = None,
) -> dict:
    return {
        "task_id": "task-1",
        "task_family": "decision_judge",
        "node_id": node,
        "model_id": model,
        "runtime_id": runtime,
        "model_artifact_sha256": (
            SHADOW_ARTIFACT if node == "xwing" else AUTH_ARTIFACT
        ),
        "task_pass": passed,
        "completion_ms": completion_ms,
        "peak_memory_bytes": memory,
        "replayed": node == "xwing",
    }


def test_faster_failed_shadow_is_never_a_latency_win() -> None:
    result = build_latency_regret_evidence(
        shadow_plan=_plan(),
        authoritative_handle=AUTH_HANDLE,
        outcomes=[
            _outcome(
                node="deathstar",
                model="ling",
                runtime="llama.cpp@ling",
                passed=True,
                completion_ms=500,
            ),
            _outcome(
                node="xwing",
                model="k2",
                runtime="llama.cpp@k2",
                passed=False,
                completion_ms=100,
            ),
        ],
    )

    assert result["quality_regret"] == "shadow_failed_quality_gate"
    assert result["latency_regret_ms"] is None
    assert result["shadow_latency_improvement_valid"] is False


def test_passing_shadow_can_show_positive_latency_regret() -> None:
    result = build_latency_regret_evidence(
        shadow_plan=_plan(),
        authoritative_handle=AUTH_HANDLE,
        outcomes=[
            _outcome(
                node="deathstar",
                model="ling",
                runtime="llama.cpp@ling",
                passed=True,
                completion_ms=500,
                memory=4_000,
            ),
            _outcome(
                node="xwing",
                model="k2",
                runtime="llama.cpp@k2",
                passed=True,
                completion_ms=300,
                memory=2_000,
            ),
        ],
    )

    assert result["quality_regret"] == "none"
    assert result["latency_regret_ms"] == 200
    assert result["memory_regret_bytes"] == 2_000
    assert result["shadow_latency_improvement_valid"] is True
    assert result["oracle_fastest_passing_handle"] == (
        f"xwing/k2/llama.cpp@k2/{SHADOW_ARTIFACT}"
    )
    assert result["actual_model_artifact_sha256"] == AUTH_ARTIFACT
    assert result["shadow_model_artifact_sha256"] == SHADOW_ARTIFACT


def test_authoritative_failure_is_recorded_as_quality_regret() -> None:
    result = build_latency_regret_evidence(
        shadow_plan=_plan(),
        authoritative_handle=AUTH_HANDLE,
        outcomes=[
            _outcome(
                node="deathstar",
                model="ling",
                runtime="llama.cpp@ling",
                passed=False,
                completion_ms=500,
            ),
            _outcome(
                node="xwing",
                model="k2",
                runtime="llama.cpp@k2",
                passed=True,
                completion_ms=300,
            ),
        ],
    )

    assert result["quality_regret"] == "authoritative_failed_shadow_passed"
    assert result["latency_regret_ms"] is None


def test_missing_shadow_outcome_remains_unknown() -> None:
    result = build_latency_regret_evidence(
        shadow_plan=_plan(),
        authoritative_handle=AUTH_HANDLE,
        outcomes=[
            _outcome(
                node="deathstar",
                model="ling",
                runtime="llama.cpp@ling",
                passed=True,
                completion_ms=500,
            )
        ],
    )

    assert result["quality_regret"] == "shadow_outcome_missing"
    assert result["shadow_realized_completion_ms"] is None
    assert result["latency_regret_ms"] is None


def test_exact_runtime_identity_prevents_same_model_alias_collision() -> None:
    with pytest.raises(ValueError, match="authoritative_handle has no outcome evidence"):
        build_latency_regret_evidence(
            shadow_plan=_plan(),
            authoritative_handle=f"deathstar/ling/llama.cpp@wrong/{AUTH_ARTIFACT}",
            outcomes=[
                _outcome(
                    node="deathstar",
                    model="ling",
                    runtime="llama.cpp@ling",
                    passed=True,
                    completion_ms=500,
                )
            ],
        )


def test_tampered_shadow_authority_is_rejected() -> None:
    plan = _plan()
    plan["authority"]["dispatch_allowed"] = True
    with pytest.raises(ValueError, match="widens authority"):
        build_latency_regret_evidence(
            shadow_plan=plan,
            authoritative_handle=AUTH_HANDLE,
            outcomes=[
                _outcome(
                    node="deathstar",
                    model="ling",
                    runtime="llama.cpp@ling",
                    passed=True,
                    completion_ms=500,
                )
            ],
        )


def test_task_family_mismatch_is_rejected() -> None:
    row = _outcome(
        node="deathstar",
        model="ling",
        runtime="llama.cpp@ling",
        passed=True,
        completion_ms=500,
    )
    row["task_family"] = "repo_work"
    with pytest.raises(ValueError, match="task_family"):
        build_latency_regret_evidence(
            shadow_plan=_plan(),
            authoritative_handle=AUTH_HANDLE,
            outcomes=[row],
        )


def test_regret_evidence_cannot_grant_authority() -> None:
    result = build_latency_regret_evidence(
        shadow_plan=_plan(),
        authoritative_handle=AUTH_HANDLE,
        outcomes=[
            _outcome(
                node="deathstar",
                model="ling",
                runtime="llama.cpp@ling",
                passed=True,
                completion_ms=500,
            ),
            _outcome(
                node="xwing",
                model="k2",
                runtime="llama.cpp@k2",
                passed=True,
                completion_ms=300,
            ),
        ],
    )
    assert set(result["authority"].values()) == {False}


def test_artifact_identity_prevents_same_runtime_alias_collision() -> None:
    row = _outcome(
        node="deathstar",
        model="ling",
        runtime="llama.cpp@ling",
        passed=True,
        completion_ms=500,
    )
    row["model_artifact_sha256"] = "c" * 64

    with pytest.raises(ValueError, match="authoritative_handle has no outcome evidence"):
        build_latency_regret_evidence(
            shadow_plan=_plan(),
            authoritative_handle=AUTH_HANDLE,
            outcomes=[row],
        )


def test_shadow_plan_without_artifact_identity_is_rejected() -> None:
    plan = _plan()
    del plan["recommended"]["model_artifact_sha256"]

    with pytest.raises(ValueError, match="exact node/model/runtime/artifact identity"):
        build_latency_regret_evidence(
            shadow_plan=plan,
            authoritative_handle=AUTH_HANDLE,
            outcomes=[
                _outcome(
                    node="deathstar",
                    model="ling",
                    runtime="llama.cpp@ling",
                    passed=True,
                    completion_ms=500,
                )
            ],
        )

