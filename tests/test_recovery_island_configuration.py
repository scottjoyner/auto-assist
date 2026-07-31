from __future__ import annotations

import json
from pathlib import Path

import pytest

from assistx.recovery_island_agent import (
    _json_mapping_from_sources,
    _read_compose_env_file,
    _subprocess_runner,
)


def test_secret_json_file_requires_private_permissions(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"key-v1": "secret"}), encoding="utf-8")
    path.chmod(0o644)

    with pytest.raises(ValueError, match="0600"):
        _json_mapping_from_sources(
            {"KEYS_FILE": str(path)},
            value_name="KEYS",
            file_name="KEYS_FILE",
            secret=True,
        )

    path.chmod(0o600)
    assert _json_mapping_from_sources(
        {"KEYS_FILE": str(path)},
        value_name="KEYS",
        file_name="KEYS_FILE",
        secret=True,
    ) == {"key-v1": "secret"}


def test_compose_environment_parser_supports_export_and_quotes(tmp_path):
    path = tmp_path / "recovery.env"
    path.write_text(
        "# recovery values\n"
        "export ASSISTX_IMAGE='assistx@sha256:abc'\n"
        'RECOVERY_BIND_ADDRESS="127.0.0.1"\n',
        encoding="utf-8",
    )

    assert _read_compose_env_file(str(path)) == {
        "ASSISTX_IMAGE": "assistx@sha256:abc",
        "RECOVERY_BIND_ADDRESS": "127.0.0.1",
    }


def test_subprocess_runner_injects_compose_environment(tmp_path, monkeypatch):
    path = tmp_path / "recovery.env"
    path.write_text("ASSISTX_IMAGE=assistx@sha256:abc\n", encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return object()

    monkeypatch.setattr(
        "assistx.recovery_island_agent.subprocess.run",
        fake_run,
    )
    run = _subprocess_runner(
        {
            "FLEET_RECOVERY_ISLAND_COMPOSE_ENV_FILE": str(path),
            "DOCKER_HOST": "unix:///run/user/1002/docker.sock",
        }
    )

    run(["docker", "compose", "config"], env={"ASSISTX_RECOVERY_ACTIVE": "1"})

    assert captured["command"] == ["docker", "compose", "config"]
    assert captured["env"]["ASSISTX_IMAGE"] == "assistx@sha256:abc"
    assert captured["env"]["DOCKER_HOST"].endswith("docker.sock")
    assert captured["env"]["ASSISTX_RECOVERY_ACTIVE"] == "1"
