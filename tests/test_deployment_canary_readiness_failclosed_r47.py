"""Fail-closed deployment-canary readiness gate (round 47).

``/api/fleet/operations-readiness`` is an untrusted HTTP evidence boundary.
An expected stage check is satisfied only by a literal JSON boolean ``true``.
Python truthiness must never turn a malformed value (a string such as
``"false"``/``"0"``, an integer, a list, or a dict) into a green readiness
verdict: this gate decides whether a staged canary proceeds.
"""

from __future__ import annotations

import pytest

from assistx.deployment_canary import (
    CACHE_CHECKS,
    CanaryFailure,
    DeploymentCanary,
    readiness_failures,
)


def _payload(ready, *, check_id="legacy_shell", detail="probe detail"):
    return {"checks": [{"id": check_id, "ready": ready, "detail": detail}]}


# Each of these is truthy in Python (or is not a boolean at all), so the
# pristine truthiness gate wrongly reports the check as ready and returns [].
_TRUTHY_NON_BOOLEAN = [
    "false",
    "true",
    "0",
    "no",
    "off",
    "False",
    "None",
    "not-ready",
    1,
    2,
    -3,
    1.0,
    [1],
    {"verdict": False},
]


@pytest.mark.parametrize("ready", _TRUTHY_NON_BOOLEAN)
def test_truthy_non_boolean_ready_is_a_failure(ready):
    failures = readiness_failures(_payload(ready), stages={"observe"})
    assert failures, f"malformed ready={ready!r} must not satisfy the gate"
    assert failures[0].startswith("legacy_shell:")
    assert "probe detail" in failures[0]


@pytest.mark.parametrize("ready", [False, None, 0, "", [], {}])
def test_falsy_ready_remains_a_failure(ready):
    assert readiness_failures(_payload(ready), stages={"observe"}) == [
        "legacy_shell: probe detail"
    ]


def test_genuine_boolean_true_is_ready():
    assert readiness_failures(_payload(True), stages={"observe"}) == []


def test_string_false_without_detail_uses_default_message():
    payload = {"checks": [{"id": "legacy_shell", "ready": "false"}]}
    assert readiness_failures(payload, stages={"observe"}) == [
        "legacy_shell: not ready"
    ]


def test_string_false_is_caught_in_a_stage_specific_check():
    payload = {
        "checks": [
            {
                "id": check_id,
                "ready": "false" if check_id == "kv_prefix_identity" else True,
                "detail": check_id,
            }
            for check_id in CACHE_CHECKS
        ]
    }
    assert readiness_failures(payload, stages={"cache"}) == [
        "kv_prefix_identity: kv_prefix_identity"
    ]


def test_missing_check_still_fails():
    assert readiness_failures({"checks": []}, stages={"observe"}) == [
        "readiness check legacy_shell is missing"
    ]


def test_mixed_payload_is_not_masked_by_a_ready_check():
    payload = {
        "checks": [
            {"id": "legacy_shell", "ready": True, "detail": "ok"},
            {"id": "control_execution", "ready": "false", "detail": "operator off"},
        ]
    }
    failures = readiness_failures(payload, stages={"observe", "recovery"})
    assert "control_execution: operator off" in failures


class _ReadinessClient:
    """In-memory HTTP stub: no socket, no service, no filesystem writes."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path))
        return 200, self.payload


def test_check_readiness_fails_closed_on_string_ready():
    client = _ReadinessClient(
        _payload("false", detail="disabled per operator")
    )
    canary = DeploymentCanary(
        client,
        node_id="node-a",
        node_token="token",
        stages={"observe"},
        run_id="run-r47",
    )
    with pytest.raises(CanaryFailure, match="disabled per operator"):
        canary.check_readiness()
    assert canary.report["steps"]["readiness"]["stage_failures"] == [
        "legacy_shell: disabled per operator"
    ]


def test_check_readiness_proceeds_on_genuine_boolean_true():
    client = _ReadinessClient(_payload(True))
    canary = DeploymentCanary(
        client,
        node_id="node-a",
        node_token="token",
        stages={"observe"},
        run_id="run-r47",
    )
    canary.check_readiness()
    assert canary.report["steps"]["readiness"]["stage_failures"] == []
