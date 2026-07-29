from types import SimpleNamespace

from assistx.recovery_runbooks import (
    RecoveryRunbookExecutor,
    build_runbook,
    validate_runbook,
)


def plan(action="restore_service", **extra):
    return {
        "action": action,
        "node_id": "node-a",
        "verify_after": ["service_online"],
        **extra,
    }


def executor(tmp_path, *, http=None, runner=None, env=None):
    return RecoveryRunbookExecutor(
        node_id="node-a",
        lmstudio_url="http://inference",
        state_dir=str(tmp_path),
        http=http or (lambda *_args, **_kwargs: (200, {"data": []})),
        runner=runner or (lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr="")),
        sleeper=lambda _: None,
        env=env or {},
    )


def test_runbook_is_targeted_bounded_and_idempotent(tmp_path):
    runbook = build_runbook(plan(), "proposal-1")

    assert validate_runbook(runbook, "node-a") is None
    assert validate_runbook(runbook, "node-b") == "runbook_target_mismatch"
    assert runbook["idempotency_key"].startswith("runbook:")
    assert len(runbook["steps"]) <= 10

    first = executor(tmp_path).execute(runbook)
    second = executor(tmp_path).execute(runbook)
    assert first["status"] == "verified"
    assert second["idempotent_replay"] is True


def test_healthy_service_short_circuits_before_restart(tmp_path):
    commands = []
    runbook = build_runbook(plan(), "proposal-healthy")
    result = executor(
        tmp_path,
        runner=lambda command, **_: commands.append(command),
    ).execute(runbook)

    assert result["ok"] is True
    assert result["verification"]["short_circuit"] is True
    assert commands == []


def test_restart_uses_allowlisted_argv_without_shell(tmp_path):
    calls = []
    responses = iter([(0, {"error": "down"}), (200, {"data": []}), (200, {"data": []})])

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="active", stderr="")

    result = executor(
        tmp_path,
        http=lambda *_args, **_kwargs: next(responses),
        runner=runner,
        env={"FLEET_RECOVERY_SERVICE_ALIASES": '{"inference":"lm-studio.service"}'},
    ).execute(build_runbook(plan(), "proposal-restart"))

    assert result["ok"] is True
    assert calls[0][0] == ["systemctl", "restart", "lm-studio.service"]
    assert all("shell" not in kwargs for _, kwargs in calls)


def test_unknown_service_alias_is_rejected(tmp_path):
    runbook = build_runbook(
        plan("restart_service", parameters={"service_alias": "malicious; reboot"}),
        "proposal-bad",
    )
    result = executor(tmp_path).execute(runbook)

    assert result["ok"] is False
    assert result["reason"] == "service_alias_not_allowlisted"


def test_drain_rolls_back_when_verification_fails(tmp_path):
    runbook = build_runbook(plan("drain_and_test"), "proposal-drain")
    result = executor(
        tmp_path,
        http=lambda *_args, **_kwargs: (503, {"error": "down"}),
    ).execute(runbook)

    assert result["ok"] is False
    assert result["rollback_results"][0]["action"] == "resume_node"
    assert not (tmp_path / "drained.json").exists()


def test_redeploy_requires_explicit_project_and_service_allowlists(tmp_path):
    project = tmp_path / "assistx"
    project.mkdir()
    calls = []
    runbook = build_runbook(
        plan("redeploy_service", parameters={"project": "assistx", "service": "api"}),
        "proposal-deploy",
    )
    result = executor(
        tmp_path / "state",
        runner=lambda command, **_: calls.append(command) or SimpleNamespace(returncode=0, stdout="", stderr=""),
        env={"FLEET_RECOVERY_COMPOSE_PROJECTS": f'{{"assistx":"{project}"}}'},
    ).execute(runbook)

    assert result["ok"] is True
    assert calls[0][:4] == ["docker", "compose", "--project-directory", str(project)]
