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


def configured(
    tmp_path,
    runner,
    *,
    deployment="assistx-shadow",
    overrides=None,
):
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
    deployment_config = {
        "project_directory": str(root),
        "project_name": "assistx_recovery_island",
        "compose_files": ["compose.recovery.yml"],
        "services": ["api"],
        "bundle_path": str(bundle),
        "manifest_path": str(manifest),
        "bundle_sha256": bundle_sha,
        "health_urls": ["http://127.0.0.1:28000/health"],
    }
    deployment_config.update(overrides or {})
    deployments = {deployment: deployment_config}
    executor = HardenedRecoveryIslandExecutor(
        node_id="beelink-recovery",
        state_dir=str(tmp_path / "state"),
        http=lambda *_args, **_kwargs: (200, {"ok": True}),
        runner=runner,
        env={
            "FLEET_RECOVERY_ISLAND_DEPLOYMENTS": json.dumps(deployments)
        },
        runbook_keys={"runbook-v1": RUNBOOK_SECRET},
        activation_keys={"activation-v1": ACTIVATION_SECRET},
    )
    return executor, bundle_sha, deployment


def runbook(
    action,
    bundle_sha,
    *,
    activation=None,
    nonce,
    deployment="assistx-shadow",
):
    parameters = {"bundle_sha256": bundle_sha}
    if activation:
        parameters["activation"] = activation
    return sign_recovery_island_runbook(
        build_recovery_island_runbook(
            action=action,
            node_id="beelink-recovery",
            deployment=deployment,
            parameters=parameters,
        ),
        key_id="runbook-v1",
        secret=RUNBOOK_SECRET,
        nonce=nonce,
    )


def activation(
    bundle_sha,
    epoch,
    nonce,
    deployment="assistx-shadow",
):
    return sign_recovery_activation(
        {
            "target_node_id": "beelink-recovery",
            "deployment": deployment,
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


def stage(
    executor,
    bundle_sha,
    deployment="assistx-shadow",
    nonce="s" * 64,
):
    return executor.execute(
        runbook(
            "stage",
            bundle_sha,
            nonce=nonce,
            deployment=deployment,
        )
    )


def activate(
    executor,
    bundle_sha,
    epoch=1,
    deployment="assistx-shadow",
    nonce="a" * 64,
):
    runbook_character = chr(117 + (ord(nonce[0]) - 97) % 10)
    return executor.execute(
        runbook(
            "activate",
            bundle_sha,
            activation=activation(
                bundle_sha,
                epoch,
                nonce,
                deployment,
            ),
            nonce=runbook_character * 64,
            deployment=deployment,
        )
    )


def test_epoch_survives_deactivation(tmp_path):
    executor, bundle_sha, deployment = configured(
        tmp_path,
        success_runner,
    )
    assert stage(executor, bundle_sha, deployment)["ok"]
    assert activate(executor, bundle_sha, 2, deployment)["ok"]
    assert executor.execute(
        runbook(
            "deactivate",
            bundle_sha,
            nonce="d" * 64,
            deployment=deployment,
        )
    )["ok"]

    stale = activate(
        executor,
        bundle_sha,
        1,
        deployment,
        nonce="b" * 64,
    )

    assert stale["ok"] is False
    assert stale["reason"] == "stale_recovery_activation_epoch"
    assert executor.status(deployment)["activation_epoch"]["epoch"] == 2


def test_stage_rejects_manifest_image_missing_after_load(tmp_path):
    def runner(command, **_kwargs):
        code = (
            1
            if command[:3] == ["docker", "image", "inspect"]
            else 0
        )
        return SimpleNamespace(
            returncode=code,
            stdout="",
            stderr="missing",
        )

    executor, bundle_sha, deployment = configured(tmp_path, runner)
    result = stage(
        executor,
        bundle_sha,
        deployment,
        nonce="m" * 64,
    )

    assert result["ok"] is False
    assert result["reason"] == "recovery_manifest_images_missing"
    assert executor.status(deployment)["status"] == "empty"


def test_subprocess_timeout_fails_closed(tmp_path):
    def runner(command, **_kwargs):
        if command[:2] == ["docker", "load"]:
            raise subprocess.TimeoutExpired(command, 10)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    executor, bundle_sha, deployment = configured(tmp_path, runner)
    result = stage(
        executor,
        bundle_sha,
        deployment,
        nonce="t" * 64,
    )

    assert result["ok"] is False
    assert result["status"] == "failed"


def test_activation_blocks_when_memory_headroom_is_insufficient(
    tmp_path,
    monkeypatch,
):
    executor, bundle_sha, deployment = configured(
        tmp_path,
        success_runner,
        deployment="assistx-durable",
        overrides={"required_available_memory_mb": 4096},
    )
    monkeypatch.setattr(executor, "_mem_available_mb", lambda: 2048)
    assert stage(executor, bundle_sha, deployment)["ok"]

    result = activate(executor, bundle_sha, deployment=deployment)

    assert result["ok"] is False
    assert result["reason"] == "insufficient_recovery_memory_headroom"


def test_activation_blocks_while_headless_model_process_is_active(
    tmp_path,
    monkeypatch,
):
    executor, bundle_sha, deployment = configured(
        tmp_path,
        success_runner,
        deployment="assistx-durable",
        overrides={
            "forbidden_process_tokens": ["lmstudio", "llama-server"]
        },
    )
    monkeypatch.setattr(executor, "_mem_available_mb", lambda: 8000)
    monkeypatch.setattr(
        executor,
        "_running_processes",
        lambda: [
            {"pid": 42, "command": "/opt/lmstudio/lms server start"}
        ],
    )
    assert stage(executor, bundle_sha, deployment)["ok"]

    result = activate(executor, bundle_sha, deployment=deployment)

    assert result["ok"] is False
    assert result["reason"] == "conflicting_host_process_active"
    assert result["matches"][0]["pid"] == 42


def test_activation_requires_continuity_tier_to_be_active(
    tmp_path,
    monkeypatch,
):
    executor, bundle_sha, deployment = configured(
        tmp_path,
        success_runner,
        deployment="assistx-durable",
        overrides={
            "requires_active_deployments": ["assistx-continuity"]
        },
    )
    monkeypatch.setattr(executor, "_mem_available_mb", lambda: 8000)
    monkeypatch.setattr(executor, "_running_processes", lambda: [])
    assert stage(executor, bundle_sha, deployment)["ok"]

    blocked = activate(executor, bundle_sha, deployment=deployment)
    assert blocked["reason"] == "required_recovery_deployment_not_active"

    executor._write_json(
        executor._active_path("assistx-continuity"),
        {"deployment": "assistx-continuity", "epoch": 1},
    )
    allowed = activate(
        executor,
        bundle_sha,
        epoch=2,
        deployment=deployment,
        nonce="c" * 64,
    )
    assert allowed["ok"] is True
