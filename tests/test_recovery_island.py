from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from assistx.recovery_island import (
    RecoveryIslandExecutor,
    build_recovery_island_runbook,
    sign_recovery_activation,
    sign_recovery_island_runbook,
    verify_recovery_activation,
)
from assistx.recovery_island_agent import execute_task

RUNBOOK_KEY = "runbook-secret"
ACTIVATION_KEY = "activation-secret"


def layout(tmp_path):
    root = tmp_path / "deployment"
    root.mkdir()
    (root / "compose.recovery.yml").write_text(
        "services:\n  api:\n    image: assistx:test\n",
        encoding="utf-8",
    )
    bundle = tmp_path / "recovery-images.tar"
    bundle.write_bytes(b"offline-image-bundle")
    bundle_sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
    manifest = tmp_path / "recovery-images.manifest.json"
    manifest.write_text(
        json.dumps(
            {"bundle_sha256": bundle_sha, "images": [{"id": "sha256:test"}]}
        ),
        encoding="utf-8",
    )
    config = {
        "assistx": {
            "project_directory": str(root),
            "project_name": "assistx_recovery_island",
            "compose_files": ["compose.recovery.yml"],
            "services": ["api"],
            "bundle_path": str(bundle),
            "manifest_path": str(manifest),
            "bundle_sha256": bundle_sha,
            "health_urls": ["http://127.0.0.1:28000/health"],
        }
    }
    return bundle_sha, config


def executor(tmp_path, *, runner=None, http=None):
    bundle_sha, config = layout(tmp_path)
    calls = []

    def default_runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    value = RecoveryIslandExecutor(
        node_id="beelink-recovery",
        state_dir=str(tmp_path / "state"),
        http=http or (lambda *_args, **_kwargs: (200, {"status": "ok"})),
        runner=runner or default_runner,
        env={
            "FLEET_RECOVERY_ISLAND_DEPLOYMENTS": json.dumps(config),
        },
        runbook_keys={"runbook-v1": RUNBOOK_KEY},
        activation_keys={"activation-v1": ACTIVATION_KEY},
    )
    return value, bundle_sha, calls


def signed_runbook(action, bundle_sha, *, activation=None, nonce="r" * 64):
    parameters = {"bundle_sha256": bundle_sha}
    if activation is not None:
        parameters["activation"] = activation
    return sign_recovery_island_runbook(
        build_recovery_island_runbook(
            action=action,
            node_id="beelink-recovery",
            deployment="assistx",
            parameters=parameters,
            proposal_id=f"proposal-{action}",
        ),
        key_id="runbook-v1",
        secret=RUNBOOK_KEY,
        nonce=nonce,
    )


def activation(bundle_sha, *, epoch=1, fence_proof="assistx-lease:lease-1"):
    return sign_recovery_activation(
        {
            "target_node_id": "beelink-recovery",
            "deployment": "assistx",
            "bundle_sha256": bundle_sha,
            "epoch": epoch,
            "fence_proof": fence_proof,
        },
        key_id="activation-v1",
        secret=ACTIVATION_KEY,
        nonce=("a" if epoch == 1 else "b") * 64,
    )


def test_stage_loads_only_checksum_pinned_offline_bundle(tmp_path):
    value, bundle_sha, calls = executor(tmp_path)

    result = value.execute(signed_runbook("stage", bundle_sha))

    assert result["ok"] is True
    assert result["status"] == "prepared"
    commands = [command for command, _ in calls]
    assert ["docker", "load", "--input"] == commands[0][:3]
    assert commands[1][-1] == "config"
    assert value.status("assistx")["status"] == "prepared"


def test_activation_requires_second_signature_and_fence_proof(tmp_path):
    value, bundle_sha, calls = executor(tmp_path)
    assert value.execute(signed_runbook("stage", bundle_sha))["ok"] is True

    missing = value.execute(
        signed_runbook("activate", bundle_sha, nonce="m" * 64)
    )
    assert missing["ok"] is False
    assert missing["reason"] == "unsupported_recovery_activation_version"

    invalid = activation(bundle_sha, fence_proof="unfenced")
    assert (
        verify_recovery_activation(
            invalid,
            {"activation-v1": ACTIVATION_KEY},
            node_id="beelink-recovery",
            deployment="assistx",
            bundle_sha256=bundle_sha,
        )
        == "missing_recovery_fence_proof"
    )

    good = value.execute(
        signed_runbook(
            "activate",
            bundle_sha,
            activation=activation(bundle_sha),
            nonce="g" * 64,
        )
    )
    assert good["ok"] is True
    assert good["status"] == "active"
    compose_up = [command for command, _ in calls if "up" in command][-1]
    assert "--no-build" in compose_up
    assert compose_up[compose_up.index("--pull") + 1] == "never"
    assert value.status("assistx")["status"] == "active"


def test_stale_activation_epoch_is_rejected(tmp_path):
    value, bundle_sha, _ = executor(tmp_path)
    value.execute(signed_runbook("stage", bundle_sha))
    first = value.execute(
        signed_runbook(
            "activate",
            bundle_sha,
            activation=activation(bundle_sha, epoch=2),
            nonce="1" * 64,
        )
    )
    assert first["ok"] is True

    stale = value.execute(
        signed_runbook(
            "activate",
            bundle_sha,
            activation=activation(bundle_sha, epoch=1),
            nonce="2" * 64,
        )
    )
    assert stale["ok"] is False
    assert stale["reason"] == "stale_recovery_activation_epoch"


def test_failed_health_check_rolls_back_activation(tmp_path):
    value, bundle_sha, calls = executor(
        tmp_path,
        http=lambda *_args, **_kwargs: (503, {"status": "down"}),
    )
    value.execute(signed_runbook("stage", bundle_sha))

    result = value.execute(
        signed_runbook(
            "activate",
            bundle_sha,
            activation=activation(bundle_sha),
            nonce="f" * 64,
        )
    )

    assert result["ok"] is False
    assert result["status"] == "rolled_back"
    assert any("stop" in command for command, _ in calls)
    assert value.status("assistx")["status"] == "prepared"


def test_dedicated_agent_requires_canonical_recovery_capability(tmp_path):
    value, bundle_sha, _ = executor(tmp_path)
    ordinary = execute_task(
        {
            "required_capabilities": ["script"],
            "payload": {"command": "echo unsafe"},
        },
        value,
    )
    assert ordinary["status"] == "FAILED"
    assert ordinary["result"]["reason"] == "recovery_capability_required"

    legacy_island_capability = execute_task(
        {
            "required_capabilities": ["recovery_island"],
            "payload": {
                "recovery_island_runbook": signed_runbook("stage", bundle_sha)
            },
        },
        value,
    )
    assert legacy_island_capability["status"] == "FAILED"
    assert (
        legacy_island_capability["result"]["reason"]
        == "recovery_capability_required"
    )


def test_dedicated_agent_rejects_ordinary_recovery_runbook(tmp_path):
    value, _, _ = executor(tmp_path)
    result = execute_task(
        {
            "required_capabilities": ["recovery"],
            "target_agent_id": "beelink-recovery",
            "payload": {"runbook": {"action": "restart_service"}},
        },
        value,
    )
    assert result["status"] == "FAILED"
    assert result["result"]["reason"] == "missing_recovery_island_runbook"


def test_dedicated_agent_rejects_wrong_target(tmp_path):
    value, bundle_sha, _ = executor(tmp_path)
    result = execute_task(
        {
            "required_capabilities": ["recovery"],
            "target_agent_id": "another-node",
            "payload": {
                "recovery_island_runbook": signed_runbook("stage", bundle_sha)
            },
        },
        value,
    )
    assert result["status"] == "FAILED"
    assert result["result"]["reason"] == "recovery_island_target_mismatch"


def test_dedicated_agent_returns_standard_verified_recovery_outcome(tmp_path):
    value, bundle_sha, _ = executor(tmp_path)
    recovery = execute_task(
        {
            "required_capabilities": ["recovery"],
            "target_agent_id": "beelink-recovery",
            "payload": {
                "recovery_island_runbook": signed_runbook("stage", bundle_sha)
            },
        },
        value,
    )
    assert recovery["status"] == "DONE"
    assert recovery["result"]["ok"] is True
    assert recovery["result"]["status"] == "verified"
    assert recovery["result"]["operation_status"] == "prepared"
    assert recovery["result"]["verification"]["ok"] is True
