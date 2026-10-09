"""Guard acceptance; does not invoke the Docker canary by default."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import pytest


def module():
    file = Path(__file__).resolve().parents[1] / "scripts/run_trace_api_isolated_canary.py"
    spec = importlib.util.spec_from_file_location("isolated_auth_runner_test", file)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_missing_opt_in_does_not_invoke_git_or_docker(monkeypatch):
    runner = module()
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("No subprocess without explicit opt-in")
    ))
    assert runner.main([]) == 2
    assert runner.main(["--expected-image-id", "sha256:" + "a" * 64]) == 2


def test_image_identity_and_mode_are_strict():
    runner = module()
    with pytest.raises(ValueError):
        runner.docker_command("redis:7-alpine", Path(__file__).resolve().parents[1],
                              "synthetic-explicit-opt-in")
    with pytest.raises(ValueError):
        runner.docker_command("sha256:" + "a" * 64, Path(__file__).resolve().parents[1], "prod")


@pytest.mark.parametrize("mode", ["synthetic-explicit-opt-in", "synthetic-trusted-header-research"])
def test_command_is_networkless_code_only_synthetic_and_immutable(mode):
    runner = module()
    image = "sha256:" + "a" * 64
    command = runner.docker_command(image, Path(__file__).resolve().parents[1], mode)
    assert command[command.index("--pull") + 1] == "never"
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--memory") + 1] == "768m"
    assert command[command.index("--cpus") + 1] == "0.5"
    assert "--read-only" in command and "--rm" in command
    assert "--cap-drop" in command and "ALL" in command
    assert command[command.index("--entrypoint") + 2] == image
    assert all("readonly" in command[i + 1] for i,v in enumerate(command)
               if v == "--mount")
    assert "-i" in command
    for forbidden in ("-p", "--publish", "-v", "--volume", "--privileged", "--network=host"):
        assert forbidden not in command
    assert "ASSISTX_TRACE_READ_BUDGET_MODE=enforce" not in command
    assert any(x == "TRUSTED_AUTH_HEADER=X-Synthetic-Proxy-Identity" for x in command) == (
        mode == "synthetic-trusted-header-research")
