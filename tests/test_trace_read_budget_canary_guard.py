"""Offline guard tests: importing these tests must never run Docker/Redis."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts/trace_read_budget_redis_canary.py"
    spec = importlib.util.spec_from_file_location("trace_redis_canary_guarded", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_with_no_approval_does_not_call_docker(monkeypatch):
    module = _module()
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("Docker must not run without explicit approval")
    ))
    assert module.main([]) == 2
    assert module.main(["--expected-image-id", "sha256:" + "a" * 64]) == 2


@pytest.mark.parametrize("expected,actual", [
    (None, None), ("abc", "abc"),
    ("sha256:" + "a" * 64, "sha256:" + "b" * 64),
])
def test_canary_requires_exact_cached_image_identity(expected, actual):
    module = _module()
    with pytest.raises(RuntimeError):
        module.validate_preflight(True, expected, actual)


def test_no_ports_mounts_network_or_image_pulls_in_docker_command():
    module = _module()
    cmd = module.docker_command("fixture-canary")
    assert cmd[:2] == ["docker", "run"]
    assert cmd[cmd.index("--network") + 1] == "none"
    assert cmd[cmd.index("--pull") + 1] == "never"
    assert cmd[cmd.index("--memory") + 1] == "96m"
    assert cmd[cmd.index("--cpus") + 1] == "0.25"
    assert "--read-only" in cmd and "--rm" in cmd
    assert "--security-opt" in cmd and "no-new-privileges" in cmd
    assert "--cap-drop" in cmd and "ALL" in cmd
    for forbidden in ("-p", "--publish", "-v", "--volume", "--network=host", "--privileged"):
        assert forbidden not in cmd


def test_script_is_the_exact_source_owned_lua_and_is_synthetic():
    module = _module()
    lua = module.extract_script()
    shell = module.test_shell(lua)
    assert "redis.call('TIME')" in lua
    assert "redis.call('ZADD'" in shell
    assert "synthetic:parallel" in shell
    assert "REDIS_LUA_CANARY_PASS" in shell
    assert "curl " not in shell and "http://" not in shell
