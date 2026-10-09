"""Offline protections for the explicitly approved disposable gateway replay canary."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

FILE = (Path(__file__).resolve().parents[1] /
        "scripts/run_gateway_real_redis_replay_isolated.py")
spec = importlib.util.spec_from_file_location("gateway_disposable_guard", FILE)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


def args(approve=False):
    return (["--approve-disposable-replay"] if approve else []) + [
        "--expected-source-sha", "a" * 40,
        "--redis-image-id", "sha256:" + "b" * 64,
    ]


def test_no_approval_never_launches_subprocess(monkeypatch):
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda *a, **kw: pytest.fail("unapproved command launched"),
    )
    assert runner.main(args()) == 2


@pytest.mark.parametrize("sha,digest", [
    ("bad", "sha256:" + "b" * 64),
    ("a" * 40, "redis:7-alpine"),
    ("a" * 40, ""),
])
def test_invalid_identifiers_deny_before_docker(monkeypatch, sha, digest):
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda *a, **kw: pytest.fail("invalid identity started a process"),
    )
    assert runner.main([
        "--approve-disposable-replay",
        "--expected-source-sha", sha,
        "--redis-image-id", digest,
    ]) == 2


def test_disposable_runner_restricts_network_and_secret_surface():
    source = FILE.read_text()
    for expected in (
        '"--network", "none"', '"--read-only"',
        '"--cap-drop", "ALL"', '"--pull", "never"',
        '"--memory", "128m"', '"--cpus", "0.25"',
        '"--save", "", "--appendonly", "no"',
        "ThreadPoolExecutor(max_workers=10)",
        "REAL_REDIS_10_WORKER_SINGLE_USE_GATEWAY_IDENTITY_PASS",
        "REAL_REDIS_RESTART_STALE_GATEWAY_ASSERTION_DENIED_PASS",
        'assert_topology(cid, expected_image_id)',
    ):
        assert expected in source
    for prohibited in (
        "NEO4J_PASSWORD", "OPENROUTER_API_KEY", "PAPERCLIP_TOKEN",
        '"--publish"', '"--privileged"', "/nas/",
        "TRUSTED_AUTH_HEADER=Tailscale-User-Login",
    ):
        assert prohibited not in source


def test_topology_verification_denies_unapproved_container(monkeypatch):
    fake = [{
        "Id": "a" * 64, "Name": "/" + runner.NAME,
        "Image": "sha256:" + "b" * 64,
        "State": {"Running": True},
        "HostConfig": {
            "NetworkMode": "git_default", "ReadonlyRootfs": True,
            "PortBindings": {}, "Binds": None, "Privileged": False,
            "Memory": 128 * 1024**2, "NanoCpus": 250_000_000,
        },
        "Mounts": [],
    }]
    monkeypatch.setattr(runner, "command", lambda *a, **kw: json.dumps(fake))
    with pytest.raises(RuntimeError, match="topology"):
        runner.assert_topology("a" * 64, "sha256:" + "b" * 64)
