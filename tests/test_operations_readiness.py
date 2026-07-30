from assistx.operations_readiness import build_operations_readiness


def test_readiness_reports_missing_requirements_without_secret_values():
    result = build_operations_readiness({})

    assert result["ready"] is False
    assert "signing_key" in result["missing"]
    assert "node_identity" in result["missing"]
    assert result["checks"][-1]["id"] == "legacy_shell"
    assert result["checks"][-1]["ready"] is True
    assert any(
        check["id"] == "improvement_attestation" and check["optional"]
        for check in result["checks"]
    )
    assert "secret" not in str(result)


def test_readiness_accepts_complete_guarded_configuration():
    result = build_operations_readiness(
        {
            "ASSISTX_RECOVERY_EXECUTION_ENABLED": "true",
            "FLEET_RECOVERY_RUNBOOKS_ENABLED": "true",
            "ASSISTX_RUNBOOK_SIGNING_KEYS": '{"v1":"signing-secret"}',
            "FLEET_RUNBOOK_VERIFY_KEYS": '{"v1":"signing-secret"}',
            "ASSISTX_RUNBOOK_ACTIVE_KEY_ID": "v1",
            "ASSISTX_FLEET_NODE_TOKENS": '{"node-a":"node-secret"}',
            "FLEET_RECOVERY_SERVICE_ALIASES": '{"inference":"service"}',
        }
    )

    assert result["ready"] is True
    assert result["safe_to_dispatch"] is True
    assert result["missing"] == []
