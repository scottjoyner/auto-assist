from assistx import fleet_node_agent


def test_generic_shell_command_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEET_UNSAFE_SHELL_TASKS_ENABLED", raising=False)
    outcome = fleet_node_agent.execute_task(
        {"payload": {"command": "touch should-not-exist"}},
        None,
        str(tmp_path),
    )

    assert outcome["status"] == "FAILED"
    assert outcome["result"]["reason"] == "unsafe_shell_tasks_disabled"
    assert not (tmp_path / "should-not-exist").exists()


def test_vision_shell_command_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEET_UNSAFE_SHELL_TASKS_ENABLED", raising=False)
    outcome = fleet_node_agent.execute_task(
        {
            "required_capabilities": ["vision"],
            "payload": {"yolo_command": "touch should-not-exist"},
        },
        None,
        str(tmp_path),
    )

    assert outcome["status"] == "FAILED"
    assert outcome["result"]["reason"] == "unsafe_shell_tasks_disabled"
    assert not (tmp_path / "should-not-exist").exists()
