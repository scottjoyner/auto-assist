from __future__ import annotations

import hashlib
import json
import subprocess
from types import SimpleNamespace

from assistx.recovery_island import (
    build_recovery_island_runbook,
    sign_recovery_activation,
    sign_recovery_island_runbook,
)
from assistx.recovery_island_hardening import HardenedRecoveryIslandExecutor

RUNBOOK_SECRET = "runbook-secret"
ACTIVATION_SECRET = "activation-secret"


def configured(tmp_path, runner):
    root = tmp_path / "deployment"
    root.mkdir()
    (root / "compose.recovery.yml").write_text(
        "services:\n  api:\n    image: assistx@test\n",
        encoding="utf-8",
    )
    bundle = tmp_path / "images.tar"
    bundle.write_bytes(b"images")
    bundle_sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "bundle_sha256": bundle_sha,
                "images": [{"id": "sha256:image-1"}],
            }
        ),
        encoding="utf-8",
    )
    deployments = {
        "assistx-shadow": {
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
    return (
        HardenedRecoveryIslandExecutor(
            node_id="beelink-recovery",
            state_dir=str(tmp_path / "state"),
            http=lambda *_args, **_kwargs: (200, {"ok": True}),
            runner=runner,
            env={"FLEET_RECOVERY_ISLAND_DEPLOYMENTS": json.dumps(deployments)},
            runbook_keys={"runbook-v1": RUNBOOK_SECRET},
            activation_keys={"activation-v1": ACTIVATION_SECRET},
        ),
        bundle_sha,
    )


def runbook(action, bundle_sha, *, activation=None, nonce):
    parameters = {"bundle_sha256": bundle_sha}
    if activation:
        parameters["activation"] = activation
    return sign_recovery_island_runbook(
        build_recovery_island_runbook(
            action=action,
            node_id="beelink-recovery",
            deployment="assistx-shadow",
            parameters=parameters,
        ),
        key_id="runbook-v1",
        secret=RUNBOOK_SECRET,
        nonce=nonce,
    )


def activation(bundle_sha, epoch, nonce):
    return sign_recovery_activation(
        {
            "target_node_id": "beelink-recovery",
            "deployment": "assistx-shadow",
            "bundle_sha256": bundle_sha,
            "epoch": epoch,
            "fence_proof": f"assistx-lease:lease-{epoch}",
        },
        key_id="activation-v1",
        secret=ACTIVATION_SECRET,
        nonce=nonce,
    )


def success_runner(_command, **_kwargs):
    return SimpleNamespace(returncode=0, stdout="ok", stderr="")


def test_epoch_survives_deactivation(tmp_path):
    executor, bundle_sha = configured(tmp_path, success_runner)
    assert executor.execute(
        runbook("stage", bundle_sha, nonce="s" * 64)
    )["ok"]
    assert executor.execute(
        runbook(
            "activate",
            bundle_sha,
            activation=activation(bundle_sha, 2, "a" * 64),
            nonce="u" * 64,
        )
    )["ok"]
    assert executor.execute(
        runbook("deactivate", bundle_sha, nonce="d" * 64)
    )["ok"]

    stale = executor.execute(
        runbook(
            "activate",
            bundle_sha,
            activation=activation(bundle_sha, 1, "b" * 64),
            nonce="v" * 64,
        )
    )

    assert stale["ok"] is False
    assert stale["reason"] == "stale_recovery_activation_epoch"
    assert executor.status("assistx-shadow")["activation_epoch"]["epoch"] == 2


def test_stage_rejects_manifest_image_missing_after_load(tmp_path):
    def runner(command, **_kwargs):
        code = 1 if command[:3] == ["docker", "image", "inspect"] else 0
        return SimpleNamespace(returncode=code, stdout="", stderr="missing")

    executor, bundle_sha = configured(tmp_path, runner)
    result = executor.execute(runbook("stage", bundle_sha, nonce="m" * 64))

    assert result["ok"] is False
    assert result["reason"] == "recovery_manifest_images_missing"
    assert executor.status("assistx-shadow")["status"] == "empty"


def test_subprocess_timeout_fails_closed(tmp_path):
    def runner(command, **_kwargs):
        if command[:2] == ["docker", "load"]:
            raise subprocess.TimeoutExpired(command, 10)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    executor, bundle_sha = configured(tmp_path, runner)
    result = executor.execute(runbook("stage", bundle_sha, nonce="t" * 64))

    assert result["ok"] is False
    assert result["status"] == "failed"
