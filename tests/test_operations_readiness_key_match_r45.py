"""Round 45 — hermetic regression: operations readiness must not report
``ready``/``safe_to_dispatch`` when the active runbook signing key and the
matching node verification key carry different material.

Pure in-process tests: only a dict is passed to build_operations_readiness().
No socket, HTTP, Neo4j, Redis, SQLite, DB/WAL, credential file, service,
container or network mount is touched, and no secret value is asserted.
"""

import json

from assistx.operations_readiness import build_operations_readiness

BASE_ENV = {
    "ASSISTX_RECOVERY_EXECUTION_ENABLED": "true",
    "FLEET_RECOVERY_RUNBOOKS_ENABLED": "true",
    "ASSISTX_FLEET_NODE_TOKENS": '{"node-a":"node-secret"}',
    "FLEET_RECOVERY_SERVICE_ALIASES": '{"inference":"service"}',
    "ASSISTX_RUNBOOK_ACTIVE_KEY_ID": "v1",
}


def _env(signing, verifying):
    return {
        **BASE_ENV,
        "ASSISTX_RUNBOOK_SIGNING_KEYS": json.dumps(signing),
        "FLEET_RUNBOOK_VERIFY_KEYS": json.dumps(verifying),
    }


def _checks(result):
    return {check["id"]: check["ready"] for check in result["checks"]}


def _ready_id(result, check_id):
    return next(
        check["ready"] for check in result["checks"] if check["id"] == check_id
    )


def test_mismatched_active_key_material_fails_closed():
    """The exact fail-before case: both key IDs exist, material differs."""
    result = build_operations_readiness(
        _env({"v1": "signing-secret"}, {"v1": "DIFFERENT-verify-secret"})
    )

    assert result["ready"] is False
    assert result["safe_to_dispatch"] is False
    assert "verification_key" in result["missing"]
    assert _ready_id(result, "verification_key") is False
    assert "signing-secret" not in str(result)
    assert "DIFFERENT-verify-secret" not in str(result)


def test_mismatched_active_keys_do_not_affect_other_checks():
    """Only the key-pair checks flip; unrelated required checks stay ready."""
    result = build_operations_readiness(
        _env({"v1": "signing-secret"}, {"v1": "DIFFERENT-verify-secret"})
    )
    checks = _checks(result)

    assert checks["control_execution"] is True
    assert checks["node_execution"] is True
    assert checks["node_identity"] is True
    assert checks["service_allowlist"] is True
    assert checks["legacy_shell"] is True


def test_matching_active_key_material_still_ready():
    """Control: genuine matching key material is unchanged."""
    result = build_operations_readiness(
        _env({"v1": "signing-secret"}, {"v1": "signing-secret"})
    )

    assert result["ready"] is True
    assert result["safe_to_dispatch"] is True
    assert result["missing"] == []
    assert _ready_id(result, "signing_key") is True
    assert _ready_id(result, "verification_key") is True


def test_verify_key_present_but_signing_key_absent_fails_closed():
    result = build_operations_readiness(_env({}, {"v1": "signing-secret"}))

    assert result["ready"] is False
    assert "signing_key" in result["missing"]
    assert "verification_key" in result["missing"]


def test_non_string_key_material_fails_closed_without_raising():
    result = build_operations_readiness(_env({"v1": 12345}, {"v1": 12345}))

    assert result["ready"] is False
    assert _ready_id(result, "verification_key") is False


def test_whitespace_only_key_material_fails_closed():
    result = build_operations_readiness(_env({"v1": "   "}, {"v1": "   "}))

    assert result["ready"] is False
    assert _ready_id(result, "verification_key") is False


def test_no_active_key_still_fails_closed():
    env = _env({"v1": "signing-secret"}, {"v1": "signing-secret"})
    env["ASSISTX_RUNBOOK_ACTIVE_KEY_ID"] = ""
    result = build_operations_readiness(env)

    assert result["ready"] is False
    assert "signing_key" in result["missing"]
    assert "verification_key" in result["missing"]
