from assistx.passive_control import (
    build_control_state,
    default_passive_control_state,
    recommended_agent_status_for_mode,
)


def test_default_passive_control_allows_work() -> None:
    state = default_passive_control_state()

    assert state["mode"] == "enabled"
    assert state["passive_allowed"] is True
    assert state["new_claims_allowed"] is True
    assert state["renewals_allowed"] is True
    assert state["recommended_agent_status"] == "idle"


def test_build_control_state_for_modes() -> None:
    enabled = build_control_state("enabled")
    draining = build_control_state("draining")
    paused = build_control_state("paused")
    maintenance = build_control_state("maintenance")

    assert enabled["new_claims_allowed"] is True
    assert enabled["renewals_allowed"] is True
    assert draining["new_claims_allowed"] is False
    assert draining["renewals_allowed"] is True
    assert paused["new_claims_allowed"] is False
    assert paused["renewals_allowed"] is False
    assert maintenance["passive_allowed"] is False


def test_recommended_agent_status_for_modes() -> None:
    assert recommended_agent_status_for_mode("enabled") == "idle"
    assert recommended_agent_status_for_mode("paused") == "paused"
    assert recommended_agent_status_for_mode("draining") == "draining"
    assert recommended_agent_status_for_mode("maintenance") == "paused"
    assert recommended_agent_status_for_mode("unknown") == "paused"
