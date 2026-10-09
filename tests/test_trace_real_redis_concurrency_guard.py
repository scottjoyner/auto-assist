"""Offline-only guards for real Redis 1/3/5/10 contention experiment."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts/run_trace_real_redis_concurrency_isolated.py"
CLIENT = ROOT / "scripts/trace_real_redis_concurrency_client.py"
spec = importlib.util.spec_from_file_location("disposable_real_redis_contention", PATH)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def args(approve=False):
    return (["--approve-isolated-redis-contention"] if approve else []) + [
        "--expected-source-sha", "a" * 40,
        "--redis-image-id", "sha256:" + "b" * 64,
        "--client-image-id", "sha256:" + "c" * 64,
    ]


def test_no_approval_stops_before_git_or_docker(monkeypatch):
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda *a, **k: pytest.fail("unapproved external process"),
    )
    assert runner.main(args()) == 2


@pytest.mark.parametrize("key,value", [
    ("--expected-source-sha", "bad"),
    ("--redis-image-id", "redis:7-alpine"),
    ("--client-image-id", "sha256:bad"),
])
def test_invalid_source_or_image_denied_without_process(monkeypatch, key, value):
    options = args(True)
    options[options.index(key) + 1] = value
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda *a, **k: pytest.fail("malformed configuration executed process"),
    )
    assert runner.main(options) == 2


def test_network_requires_internal_and_no_unapproved_peers(monkeypatch):
    net = {
        "Id": "1" * 64, "Name": runner.NETWORK,
        "Internal": True, "Attachable": False, "Ingress": False,
        "Containers": {},
    }
    monkeypatch.setattr(runner, "output", lambda *a, **k: json.dumps([net]))
    runner.network_exact("1" * 64, set())
    net["Internal"] = False
    with pytest.raises(RuntimeError):
        runner.network_exact("1" * 64, set())
    net["Internal"] = True
    net["Containers"] = {"foreign": {"Name": "unapproved-peer"}}
    with pytest.raises(RuntimeError):
        runner.network_exact("1" * 64, set())


def test_client_mount_policy_is_readonly_and_exact(monkeypatch):
    item = {
        "Id": "a" * 64, "Name": "/" + runner.CLIENT,
        "Image": "sha256:" + "b" * 64,
        "HostConfig": {
            "NetworkMode": runner.NETWORK,
            "PortBindings": {}, "Binds": None,
            "ReadonlyRootfs": True, "Privileged": False,
        },
        "Mounts": [
            {"Destination": "/work/src", "Type": "bind", "RW": False},
            {"Destination": "/work/canary.py", "Type": "bind", "RW": False},
        ],
    }
    monkeypatch.setattr(runner, "output", lambda *a, **k: json.dumps([item]))
    runner.container_exact("a" * 64, runner.CLIENT, "sha256:" + "b" * 64)
    item["Mounts"][1]["RW"] = True
    with pytest.raises(RuntimeError):
        runner.container_exact("a" * 64, runner.CLIENT, "sha256:" + "b" * 64)


def test_bounded_real_contention_contract_is_source_only():
    text = CLIENT.read_text()
    harness = PATH.read_text()
    assert "for n in (1, 3, 5, 10)" in text
    assert "fleet_inflight=3" in text
    assert "fleet_rate=60" in text
    assert "release(client, forged) is False" in text
    assert "client.zcard(KEY) == 0" in text
    assert "REAL_REDIS_1_3_5_10_CONCURRENCY_FENCE_PASS" in text
    for needed in (
        '"--internal"', '"--read-only"',
        '"--cap-drop", "ALL"', '"--pull", "never"',
        "args.redis_image_id", "args.client_image_id",
        "ASSISTX_DISPOSABLE_REDIS_CONTENTION=synthetic-explicit-opt-in",
    ):
        assert needed in harness
    for forbidden in ("NEO4J_PASSWORD=", "PAPERCLIP_TOKEN=",
                      "OPENAI_API_KEY=", '"--publish"', "/nas/"):
        assert forbidden not in text + harness
