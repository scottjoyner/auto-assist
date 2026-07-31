from __future__ import annotations

import pytest

from assistx.recovery_island_routes import (
    RecoveryIslandRequestIn,
    _auto_approval_allowed,
    _island_plan,
    _token_authorized,
)

BUNDLE_SHA = "a" * 64


def request(action="stage", deployment="assistx-shadow", bundle_sha=BUNDLE_SHA):
    return RecoveryIslandRequestIn(
        request_id=f"request-{action}-001",
        node_id="beelink-recovery",
        deployment=deployment,
        action=action,
        reason="primary control plane failed health verification",
        bundle_sha256=bundle_sha,
        metadata={"incident_key": "incident-1"},
    )


def test_stage_and_activate_require_bundle_checksum():
    with pytest.raises(ValueError, match="bundle_sha256"):
        _island_plan(request(action="stage", bundle_sha=None))
    with pytest.raises(ValueError, match="bundle_sha256"):
        _island_plan(request(action="activate", bundle_sha=None))


def test_plan_is_target_pinned_and_rollback_bounded():
    plan = _island_plan(request())
    island = plan["parameters"]["recovery_island"]

    assert plan["node_id"] == "beelink-recovery"
    assert island["deployment"] == "assistx-shadow"
    assert island["bundle_sha256"] == BUNDLE_SHA
    assert plan["rollback"] == {
        "action": "deactivate",
        "deployment": "assistx-shadow",
    }


def test_request_token_is_fail_closed():
    assert _token_authorized("", "anything") is False
    assert _token_authorized("expected", None) is False
    assert _token_authorized("expected", "wrong") is False
    assert _token_authorized("expected", "expected") is True


def test_auto_approval_requires_token_actor_and_action(monkeypatch):
    monkeypatch.setenv(
        "ASSISTX_RECOVERY_ISLAND_AUTO_APPROVE_ACTORS",
        "healer-agent",
    )
    monkeypatch.setenv(
        "ASSISTX_RECOVERY_ISLAND_AUTO_APPROVE_ACTIONS",
        "stage,verify",
    )

    assert _auto_approval_allowed(
        request(), actor="healer-agent", token_authorized=False
    ) == (False, "agent_request_token_not_authorized")
    assert _auto_approval_allowed(
        request(), actor="other-agent", token_authorized=True
    ) == (False, "actor_not_auto_approved")
    assert _auto_approval_allowed(
        request(action="deactivate", bundle_sha=None),
        actor="healer-agent",
        token_authorized=True,
    ) == (False, "action_not_auto_approved")
    assert _auto_approval_allowed(
        request(), actor="healer-agent", token_authorized=True
    ) == (True, "policy_allowed")


def test_activation_is_limited_to_shadow_deployment(monkeypatch):
    monkeypatch.setenv(
        "ASSISTX_RECOVERY_ISLAND_AUTO_APPROVE_ACTORS",
        "healer-agent",
    )
    monkeypatch.setenv(
        "ASSISTX_RECOVERY_ISLAND_AUTO_APPROVE_ACTIONS",
        "activate",
    )
    monkeypatch.setenv(
        "ASSISTX_RECOVERY_ISLAND_AUTO_ACTIVATION_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "ASSISTX_RECOVERY_ISLAND_AUTO_ACTIVATION_DEPLOYMENTS",
        "assistx-shadow",
    )

    assert _auto_approval_allowed(
        request(action="activate"),
        actor="healer-agent",
        token_authorized=True,
    ) == (True, "policy_allowed")
    assert _auto_approval_allowed(
        request(action="activate", deployment="assistx-executor"),
        actor="healer-agent",
        token_authorized=True,
    ) == (False, "deployment_not_auto_activatable")
