"""Offline Docker custody preflight for real-Redis/Neo4j research canary.

Tests never start Docker or call live graph/Redis.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import pytest

FILE = Path(__file__).resolve().parents[1] / "scripts/run_trace_real_redis_neo4j_cancel_isolated.py"
spec = importlib.util.spec_from_file_location("trace_redis_neo_guard", FILE)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def argv(approval: bool = False):
    return (["--approve-disposable-combined-probe"] if approval else []) + [
        "--expected-source-sha", "a" * 40,
        "--neo-image-id", "sha256:" + "b" * 64,
        "--redis-image-id", "sha256:" + "c" * 64,
        "--client-image-id", "sha256:" + "d" * 64,
    ]


def test_no_approval_never_executes_process(monkeypatch):
    monkeypatch.setattr(
        guard.subprocess, "run",
        lambda *a, **kw: pytest.fail("unexpected Docker/Git operation"),
    )
    assert guard.main(argv()) == 2


@pytest.mark.parametrize("bad", [
    ["--expected-source-sha", "no"],
    ["--neo-image-id", "redis:7-alpine"],
    ["--redis-image-id", "sha256:bad"],
])
def test_bad_exact_id_refused_before_any_work(monkeypatch, bad):
    cmd = argv(True)
    cmd[cmd.index(bad[0]) + 1] = bad[1]
    monkeypatch.setattr(
        guard.subprocess, "run",
        lambda *a, **kw: pytest.fail("unexpected process launched"),
    )
    assert guard.main(cmd) == 2


def test_real_run_guard_disallows_production_endpoints():
    source = FILE.read_text()
    client = (FILE.parent / "trace_real_redis_neo4j_cancel_client.py").read_text()
    for marker in (
        '"--internal"', '"--pull", "never"', '"--read-only"',
        '"--cap-drop", "ALL"', '"--pids-limit"',
        '"--mount", f"type=bind,source={CLIENT_SCRIPT},target=/work/canary.py,readonly"',
        'container_exact(owned[REDIS]', 'network_exact(network_id',
        'cmd("docker", "restart", "-t", "1", owned[REDIS]',
    ):
        assert marker in source
    assert 'ASSISTX_DISPOSABLE_REDIS_NEO_CANARY' in client
    assert "timeout=3.0" in client
    assert "SHOW TRANSACTIONS" in client
    assert "NEO4J_PASSWORD" not in source + client
    assert "BASIC_AUTH_PASS" not in source + client
    assert "Tailscale-User-Login" not in source + client


def test_rejects_external_network_metadata(monkeypatch):
    network = {
        "Id": "1" * 64, "Name": guard.NETWORK,
        "Internal": False, "Attachable": False, "Ingress": False,
        "Containers": {},
    }
    monkeypatch.setattr(guard, "cmd", lambda *a, **kw: json.dumps([network]))
    with pytest.raises(RuntimeError, match="noninternal"):
        guard.network_exact("1" * 64, set())


def test_rejects_unexpected_peer(monkeypatch):
    network = {
        "Id": "1" * 64, "Name": guard.NETWORK,
        "Internal": True, "Attachable": False, "Ingress": False,
        "Containers": {"a": {"Name": "unapproved"}},
    }
    monkeypatch.setattr(guard, "cmd", lambda *a, **kw: json.dumps([network]))
    with pytest.raises(RuntimeError, match="unexpected network peer"):
        guard.network_exact("1" * 64, set())


def test_rejects_wrong_docker_server_image(monkeypatch):
    item = {
        "Id": "a" * 64, "Name": "/" + guard.NEO,
        "Image": "sha256:" + "b" * 64,
        "State": {"Running": True},
        "HostConfig": {
            "NetworkMode": guard.NETWORK, "PortBindings": {},
            "Binds": None,
        },
        "Mounts": [],
    }
    monkeypatch.setattr(guard, "cmd", lambda *a, **kw: json.dumps([item]))
    with pytest.raises(RuntimeError, match="identity"):
        guard.container_exact("a" * 64, guard.NEO,
                              "sha256:" + "c" * 64, guard.NETWORK)
