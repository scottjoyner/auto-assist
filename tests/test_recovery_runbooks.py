from types import SimpleNamespace

from assistx.recovery_runbooks import (
    RecoveryRunbookExecutor,
    build_runbook,
    sign_runbook,
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
        verify_keys={"test-v1": "test-secret"},
    )


def signed(value):
    return sign_runbook(
        value,
        key_id="test-v1",
        secret="test-secret",
        ttl_seconds=900,
        nonce="n" * 64,
    )


def test_runbook_is_targeted_bounded_and_idempotent(tmp_path):
    runbook = signed(build_runbook(plan(), "proposal-1"))

    assert validate_runbook(runbook, "node-a") is None
    assert validate_runbook(runbook, "node-b") == "runbook_target_mismatch"
    assert runbook["idempotency_key"].startswith("runbook:")
    assert len(runbook["steps"]) <= 10

    first = executor(tmp_path)
    first_result = first.execute(runbook)
    second = executor(tmp_path).execute(runbook)
    assert first_result["status"] == "verified"
    assert second["idempotent_replay"] is True


def test_healthy_service_short_circuits_before_restart(tmp_path):
    commands = []
    runbook = signed(build_runbook(plan(), "proposal-healthy"))
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
    ).execute(signed(build_runbook(plan(), "proposal-restart")))

    assert result["ok"] is True
    assert ["systemctl", "restart", "lm-studio.service"] in [command for command, _ in calls]
    assert all("shell" not in kwargs for _, kwargs in calls)


def test_unknown_service_alias_is_rejected(tmp_path):
    runbook = signed(build_runbook(
        plan("restart_service", parameters={"service_alias": "malicious; reboot"}),
        "proposal-bad",
    ))
    result = executor(tmp_path).execute(runbook)

    assert result["ok"] is False
    assert result["reason"] == "service_alias_not_allowlisted"


def test_drain_rolls_back_when_verification_fails(tmp_path):
    runbook = signed(build_runbook(plan("drain_and_test"), "proposal-drain"))
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
    runbook = signed(build_runbook(
        plan(
            "redeploy_service",
            parameters={
                "project": "assistx",
                "service": "api",
                "image_env": "ASSISTX_API_IMAGE",
                "image_digest": "ghcr.io/example/assistx@sha256:" + "a" * 64,
            },
        ),
        "proposal-deploy",
    ))
    result = executor(
        tmp_path / "state",
        runner=lambda command, **_: calls.append(command) or SimpleNamespace(returncode=0, stdout="", stderr=""),
        env={"FLEET_RECOVERY_COMPOSE_PROJECTS": f'{{"assistx":"{project}"}}'},
    ).execute(runbook)

    assert result["ok"] is True
    assert any(call[:4] == ["docker", "compose", "--project-directory", str(project)] for call in calls)


def test_unsigned_tampered_and_expired_runbooks_are_rejected(tmp_path):
    unsigned = build_runbook(plan(), "proposal-unsigned")
    assert executor(tmp_path / "unsigned").execute(unsigned)["reason"] == "missing_runbook_attestation"

    tampered = signed(build_runbook(plan(), "proposal-tampered"))
    tampered["steps"][0]["action"] = "resume_node"
    assert executor(tmp_path / "tampered").execute(tampered)["reason"] == "runbook_signature_mismatch"

    expired = sign_runbook(
        build_runbook(plan(), "proposal-expired"),
        key_id="test-v1",
        secret="test-secret",
        now=1,
        ttl_seconds=30,
        nonce="e" * 64,
    )
    assert executor(tmp_path / "expired").execute(expired)["reason"] == "attestation_expired_or_invalid"


def test_launchd_adapter_uses_typed_commands(tmp_path):
    calls = []
    runbook = signed(build_runbook(plan(), "proposal-launchd"))
    responses = iter([(503, {}), (200, {"data": []}), (200, {"data": []})])

    result = executor(
        tmp_path,
        http=lambda *_args, **_kwargs: next(responses),
        runner=lambda command, **_: calls.append(command) or SimpleNamespace(returncode=0, stdout="", stderr=""),
        env={"FLEET_RECOVERY_SERVICE_ALIASES": '{"inference":{"adapter":"launchd","label":"com.example.inference"}}'},
    ).execute(runbook)

    assert result["ok"] is True
    assert ["launchctl", "kickstart", "-k", "system/com.example.inference"] in calls


def test_observation_only_adapter_refuses_mutation(tmp_path):
    runbook = signed(build_runbook(plan(), "proposal-observe"))
    responses = iter([(503, {})])

    result = executor(
        tmp_path,
        http=lambda *_args, **_kwargs: next(responses),
        env={"FLEET_RECOVERY_SERVICE_ALIASES": '{"inference":{"adapter":"observation","instructions":"restart manually"}}'},
    ).execute(runbook)

    assert result["ok"] is False
    assert result["reason"] == "observation_only_adapter"
