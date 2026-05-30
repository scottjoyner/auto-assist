from assistx.passive_agents import (
    _advisory_lease,
    _candidate_safety,
    _capabilities_match,
    _heartbeat_counts,
    _is_passive_safe,
    _next_heartbeat_seconds,
    _normalize_idle_candidate,
    _normalize_status,
    _plan_for_status,
    _rank_candidate,
)


def test_normalize_status_defaults_unknown_to_idle() -> None:
    assert _normalize_status("busy") == "busy"
    assert _normalize_status("draining") == "draining"
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
    assert plan["lease"]["lease_type"] == "advisory_only"


def test_plan_for_idle_resumes_current_before_new_candidate() -> None:
    plan = _plan_for_status(
        status="idle",
        current_task={"task_id": "t1", "resume_policy": "preserve_and_resume_when_idle"},
        candidates=[{"task_id": "t2"}],
        mode="passive",
    )

    assert plan["action"] == "resume_current"
    assert plan["recommended_task_id"] == "t1"


def test_plan_for_idle_recommends_review_candidate() -> None:
    plan = _plan_for_status(
        status="idle",
        current_task=None,
        candidates=[{"task_id": "t2", "safety": {"passive_safe": True}}],
        mode="passive",
    )

    assert plan["action"] == "review_next_candidate"
    assert plan["recommended_task_id"] == "t2"
    assert plan["lease"]["claim_required_before_execution"] is True


def test_plan_for_claim_ready_still_requires_approved_claim_api() -> None:
    plan = _plan_for_status(
        status="idle",
        current_task=None,
        candidates=[{"task_id": "t2"}],
        mode="claim_ready",
    )

    assert plan["action"] == "recommend_claim_via_approved_endpoint"
    assert plan["lease"]["lease_type"] == "advisory_only"


def test_user_active_yields_to_user_work() -> None:
    plan = _plan_for_status(
        status="busy",
        current_task={"task_id": "background-1"},
        candidates=[{"task_id": "next"}],
        mode="passive",
        user_active=True,
    )

    assert plan["action"] == "yield_to_user"
    assert plan["resume"]["task_id"] == "background-1"
    assert plan["next_heartbeat_seconds"] == 10


def test_draining_finishes_smallest_checkpoint() -> None:
    plan = _plan_for_status(
        status="draining",
        current_task={"task_id": "background-1"},
        candidates=[],
        mode="passive",
        max_work_seconds=900,
    )

    assert plan["action"] == "finish_current_step_then_pause"
    assert plan["lease"]["max_work_seconds"] == 300


def test_advisory_lease_clamps_work_window() -> None:
    lease = _advisory_lease("t1", "passive", 1000, 999999)

    assert lease["task_id"] == "t1"
    assert lease["max_work_seconds"] == 7200
    assert lease["claim_required_before_execution"] is True


def test_normalize_idle_candidate_marks_private_not_safe() -> None:
    item = _normalize_idle_candidate(
        {
            "id": "t1",
            "title": "Private",
            "status": "READY",
            "payload_json": '{"privacy":"private","queue_class":"batch"}',
        },
        now_ms=2000,
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


def test_candidate_rank_and_safety_explain_decision() -> None:
    item = {
        "task_id": "t1",
        "status": "REVIEW",
        "priority": "HIGH",
        "queue_class": "batch",
        "age_seconds": 7200,
        "required_capabilities": ["docs"],
        "local_only": False,
        "sensitive": False,
        "privacy": "",
    }

    assert _rank_candidate(item, 1000) < 1000
    safety = _candidate_safety(item)
    assert safety["passive_safe"] is True
    assert safety["requires_claim_before_execution"] is True
    assert safety["write_allowed"] is False


def test_heartbeat_counts() -> None:
    assert _heartbeat_counts([{"status": "idle"}, {"status": "busy"}, {"status": "busy"}]) == {
        "total": 3,
        "idle": 1,
        "busy": 2,
    }


def test_next_heartbeat_seconds() -> None:
    assert _next_heartbeat_seconds("idle") == 45
    assert _next_heartbeat_seconds("busy") == 60
    assert _next_heartbeat_seconds("idle", user_active=True) == 10
