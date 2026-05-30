from assistx.passive_status import passive_system_recommendations


def test_passive_status_recommends_expiring_stale_claims() -> None:
    recs = passive_system_recommendations(
        heartbeat_summary={"total": 1, "idle": 1},
        claim_summary={"total": 1, "active": 0, "expired": 1},
        idle_work=[],
    )

    assert recs[0]["action"] == "expire_stale_claims"


def test_passive_status_recommends_idle_agents_when_work_exists() -> None:
    recs = passive_system_recommendations(
        heartbeat_summary={"total": 1, "idle": 1},
        claim_summary={"total": 0, "active": 0, "expired": 0},
        idle_work=[{"task_id": "t1"}],
    )

    assert any(rec["action"] == "heartbeat_idle_agents" for rec in recs)


def test_passive_status_recommends_monitoring_busy_agents() -> None:
    recs = passive_system_recommendations(
        heartbeat_summary={"total": 1, "busy": 1},
        claim_summary={"total": 1, "active": 1, "expired": 0},
        idle_work=[],
    )

    assert any(rec["action"] == "monitor_claim_renewals" for rec in recs)


def test_passive_status_idle_wait_when_no_work_and_no_claims() -> None:
    recs = passive_system_recommendations(
        heartbeat_summary={"total": 0},
        claim_summary={"total": 0, "active": 0, "expired": 0},
        idle_work=[],
    )

    assert recs == [
        {
            "level": "info",
            "action": "idle_wait",
            "reason": "no active passive claims and no passive-safe idle work found",
        }
    ]


def test_passive_status_respects_paused_control() -> None:
    recs = passive_system_recommendations(
        heartbeat_summary={"total": 1, "idle": 1},
        claim_summary={"total": 0, "active": 0, "expired": 0},
        idle_work=[{"task_id": "t1"}],
        control={"mode": "paused", "new_claims_allowed": False, "renewals_allowed": False},
    )

    assert recs[0]["action"] == "keep_agents_paused"
    assert not any(rec["action"] == "heartbeat_idle_agents" for rec in recs)


def test_passive_status_respects_draining_control() -> None:
    recs = passive_system_recommendations(
        heartbeat_summary={"total": 1, "busy": 1},
        claim_summary={"total": 1, "active": 1, "expired": 0},
        idle_work=[{"task_id": "t1"}],
        control={"mode": "draining", "new_claims_allowed": False, "renewals_allowed": True},
    )

    assert recs[0]["action"] == "drain_current_work"
    assert any(rec["action"] == "monitor_claim_renewals" for rec in recs)
    assert not any(rec["action"] == "heartbeat_idle_agents" for rec in recs)
