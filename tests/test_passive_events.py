from assistx.passive_events import passive_event_summary


def test_passive_event_summary_counts_by_type_and_agent() -> None:
    summary = passive_event_summary(
        [
            {"event_type": "passive_claim.created", "agent_id": "a1"},
            {"event_type": "passive_claim.renewed", "agent_id": "a1"},
            {"event_type": "passive_claim.created", "agent_id": "a2"},
            {"event_type": None, "agent_id": None},
        ]
    )

    assert summary["total"] == 4
    assert summary["by_type"]["passive_claim.created"] == 2
    assert summary["by_type"]["passive_claim.renewed"] == 1
    assert summary["by_type"]["unknown"] == 1
    assert summary["by_agent"]["a1"] == 2
    assert summary["by_agent"]["a2"] == 1
    assert summary["by_agent"]["unknown"] == 1
