from assistx.passive_claims import (
    PassiveClaimIn,
    PassiveClaimRenewIn,
    _blocked,
    _claim_from_task,
    _next_status_for_release,
    passive_claim_summary,
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
    assert _next_status_for_release("expired") == "READY"
    assert _next_status_for_release("unknown") == "READY"


def test_passive_claim_defaults_are_review_only_safe() -> None:
    body = PassiveClaimIn(agent_id="agent", task_id="task")

    assert body.mode == "review_only"
    assert body.operator_approved is False
    assert body.ttl_seconds == 1800


def test_passive_claim_renew_defaults_are_safe() -> None:
    body = PassiveClaimRenewIn(agent_id="agent", task_id="task", claim_id="claim")

    assert body.ttl_seconds == 1800
    assert body.progress_note is None
    assert body.metadata == {}


def test_claim_from_task_marks_active_and_remaining_seconds() -> None:
    claim = _claim_from_task(
        {
            "id": "task-1",
            "title": "Review docs",
            "status": "CLAIMED_PASSIVE",
            "passive_claim_id": "claim-1",
            "passive_claim_agent_id": "agent-1",
            "passive_claim_lease_id": "lease-1",
            "passive_claim_mode": "review_only",
            "passive_claimed_at_ts": 1000,
            "passive_claim_expires_at_ts": 61000,
            "passive_claim_renewed_at_ts": 3000,
            "passive_claim_progress_note": "still reviewing",
        },
        now_ms=1000,
    )

    assert claim["task_id"] == "task-1"
    assert claim["expired"] is False
    assert claim["seconds_remaining"] == 60
    assert claim["renewed_at_ts"] == 3000
    assert claim["progress_note"] == "still reviewing"


def test_claim_from_task_marks_expired() -> None:
    claim = _claim_from_task(
        {
            "id": "task-1",
            "status": "CLAIMED_PASSIVE",
            "passive_claim_expires_at_ts": 1000,
        },
        now_ms=2000,
    )

    assert claim["expired"] is True
    assert claim["seconds_remaining"] == 0


def test_passive_claim_summary_counts_states() -> None:
    summary = passive_claim_summary(
        [
            {"expired": False, "mode": "review_only"},
            {"expired": True, "mode": "passive"},
            {"expired": False, "mode": "claim_ready"},
        ]
    )

    assert summary == {"total": 3, "active": 2, "expired": 1, "review_only": 2, "claim_ready": 1}
