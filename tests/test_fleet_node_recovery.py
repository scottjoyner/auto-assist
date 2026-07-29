from assistx import fleet_node_agent
from assistx.recovery_runbooks import build_runbook, sign_runbook


def signed(runbook):
    return sign_runbook(
        runbook,
        key_id="test-v1",
        secret="test-secret",
        nonce="n" * 64,
    )


def test_recovery_task_uses_typed_executor_before_command(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_RECOVERY_RUNBOOKS_ENABLED", "true")
    monkeypatch.setenv("FLEET_RUNBOOK_VERIFY_KEYS", '{"test-v1":"test-secret"}')
    monkeypatch.setattr(
        fleet_node_agent,
        "_http",
        lambda *_args, **_kwargs: (200, {"data": []}),
    )
    task = {
        "required_capabilities": ["recovery"],
        "payload": {
            "command": "this must never execute",
            "runbook": signed(build_runbook(
                {"action": "health_check", "node_id": "node-a"},
                "proposal",
            )),
        },
    }

    outcome = fleet_node_agent.execute_task(
        task,
        "http://inference",
        str(tmp_path),
        node_id="node-a",
    )

    assert outcome["status"] == "DONE"
    assert outcome["result"]["status"] == "verified"


def test_recovery_task_rejects_wrong_target(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEET_RECOVERY_RUNBOOKS_ENABLED", "true")
    monkeypatch.setenv("FLEET_RUNBOOK_VERIFY_KEYS", '{"test-v1":"test-secret"}')
    task = {
        "required_capabilities": ["recovery"],
        "payload": {
            "runbook": signed(build_runbook(
                {"action": "health_check", "node_id": "another-node"},
                "proposal",
            )),
        },
    }

    outcome = fleet_node_agent.execute_task(
        task,
        "http://inference",
        str(tmp_path),
        node_id="node-a",
    )

    assert outcome["status"] == "FAILED"
    assert outcome["result"]["reason"] == "runbook_target_mismatch"
