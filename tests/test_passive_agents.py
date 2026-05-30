from assistx.passive_agents import (
    _capabilities_match,
    _is_passive_safe,
    _normalize_idle_candidate,
    _normalize_status,
    _plan_for_status,
)


def test_normalize_status_defaults_unknown_to_idle() -> None:
    assert _normalize_status("busy") == "busy"
    assert _normalize_status("weird") == "idle"


def test_plan_for_busy_preserves_current_task() -> None:
    plan = _plan_for_status(
        status="busy",
        current_task={"task_id": "t1", "resume_policy": "preserve_and_resume_when_idle"},
        candidates=[{"task_id": "t2"}],
        mode="passive",
    )

    assert plan["action"] == "continue_current"
    assert plan["resume"]["task_id"] == "t1"


def test_plan_for_idle_recommends_review_candidate() -> None:
    plan = _plan_for_status(
        status="idle",
        current_task=None,
        candidates=[{"task_id": "t2"}],
        mode="passive",
    )

    assert plan["action"] == "review_next_candidate"
    assert plan["recommended_task_id"] == "t2"


def test_plan_for_claim_ready_still_requires_approved_claim_api() -> None:
    plan = _plan_for_status(
        status="idle",
        current_task=None,
        candidates=[{"task_id": "t2"}],
        mode="claim_ready",
    )

    assert plan["action"] == "recommend_claim_via_approved_endpoint"


def test_normalize_idle_candidate_marks_private_not_safe() -> None:
    item = _normalize_idle_candidate(
        {
            "id": "t1",
            "title": "Private",
            "status": "READY",
            "payload_json": '{"privacy":"private","queue_class":"batch"}',
        }
    )

    assert item["local_only"] is True
    assert item["sensitive"] is True
    assert _is_passive_safe(item) is False


def test_passive_safe_rejects_interactive_and_accepts_batch() -> None:
    assert _is_passive_safe({"status": "READY", "queue_class": "interactive"}) is False
    assert _is_passive_safe({"status": "READY", "queue_class": "batch"}) is True


def test_capabilities_match_required_subset() -> None:
    assert _capabilities_match({"required_capabilities": ["code", "terminal"]}, ["code", "terminal", "web"]) is True
    assert _capabilities_match({"required_capabilities": ["code", "terminal"]}, ["code"]) is False
    assert _capabilities_match({"required_capabilities": []}, []) is True
