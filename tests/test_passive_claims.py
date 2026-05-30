from assistx.passive_claims import (
    PassiveClaimIn,
    _blocked,
    _next_status_for_release,
)


def test_passive_claim_rejects_invalid_mode() -> None:
    body = PassiveClaimIn(agent_id="a1", task_id="t1", mode="execute")
    result = _blocked("invalid_mode", f"mode {body.mode!r} is not allowed for passive claims")

    assert result["ok"] is False
    assert result["reason"] == "invalid_mode"


def test_claim_ready_requires_operator_approval_shape() -> None:
    body = PassiveClaimIn(agent_id="a1", task_id="t1", mode="claim_ready", operator_approved=False)

    assert body.mode == "claim_ready"
    assert body.operator_approved is False


def test_next_status_for_release() -> None:
    assert _next_status_for_release("completed_review") == "REVIEW"
    assert _next_status_for_release("interrupted") == "READY"
    assert _next_status_for_release("abandoned") == "READY"
    assert _next_status_for_release("released") == "READY"
    assert _next_status_for_release("unknown") == "READY"


def test_passive_claim_defaults_are_review_only_safe() -> None:
    body = PassiveClaimIn(agent_id="agent", task_id="task")

    assert body.mode == "review_only"
    assert body.operator_approved is False
    assert body.ttl_seconds == 1800
