"""Offline safety checks for disposable real Redis multiworker research."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_trace_real_redis_multiworker_isolated.py"
CLIENT = ROOT / "scripts/trace_real_redis_multiworker_client.py"
spec = importlib.util.spec_from_file_location("trace_redis_mw_guard", RUNNER)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def args(approve=False):
    return (["--approve-disposable-multiworker"] if approve else []) + [
        "--expected-source-sha", "a" * 40,
        "--redis-image-id", "sha256:" + "b" * 64,
        "--client-image-id", "sha256:" + "c" * 64,
    ]


def test_no_explicit_opt_in_runs_no_commands(monkeypatch):
    monkeypatch.setattr(
        guard.subprocess, "run",
        lambda *a, **kw: pytest.fail("unapproved process"),
    )
    monkeypatch.setattr(guard.argparse.ArgumentParser, "parse_args",
                        lambda self: guard.argparse.Namespace(
                            approve_disposable_multiworker=False,
                            expected_source_sha="a" * 40,
                            redis_image_id="sha256:" + "b" * 64,
                            client_image_id="sha256:" + "c" * 64,
                        ))
    assert guard.main() == 2


@pytest.mark.parametrize("source,redis_id,client_id", [
    ("not-sha", "sha256:" + "b" * 64, "sha256:" + "c" * 64),
    ("a" * 40, "redis:7-alpine", "sha256:" + "c" * 64),
    ("a" * 40, "sha256:" + "b" * 64, "unknown"),
])
def test_invalid_source_or_image_fails_before_docker(
    monkeypatch, source, redis_id, client_id,
):
    monkeypatch.setattr(guard.subprocess, "run",
                        lambda *a, **kw: pytest.fail("malformed approval ran process"))
    monkeypatch.setattr(guard.argparse.ArgumentParser, "parse_args",
                        lambda self: guard.argparse.Namespace(
                            approve_disposable_multiworker=True,
                            expected_source_sha=source,
                            redis_image_id=redis_id,
                            client_image_id=client_id,
                        ))
    assert guard.main() == 2


def test_external_network_is_not_accepted(monkeypatch):
    network = [{"Id": "a" * 64, "Name": guard.NET,
                "Internal": False, "Attachable": False, "Ingress": False,
                "Containers": {}}]
    monkeypatch.setattr(guard, "cmd", lambda *a, **kw: json.dumps(network))
    with pytest.raises(RuntimeError, match="network identity"):
        guard.witness_network("a" * 64, set())


def test_unknown_network_peer_is_rejected(monkeypatch):
    network = [{"Id": "a" * 64, "Name": guard.NET,
                "Internal": True, "Attachable": False, "Ingress": False,
                "Containers": {"x": {"Name": "unapproved"}}}]
    monkeypatch.setattr(guard, "cmd", lambda *a, **kw: json.dumps(network))
    with pytest.raises(RuntimeError, match="membership"):
        guard.witness_network("a" * 64, set())


def test_only_immutable_local_images_and_readonly_scratch_workload():
    text = RUNNER.read_text()
    client = CLIENT.read_text()
    for required in (
        '"--internal"', '"--read-only"', '"--pull", "never"',
        '"--cap-drop", "ALL"', '"--pids-limit", "64"',
        'images["redis"]', 'images["client"]',
        'ASSISTX_DISPOSABLE_REDIS_MULTIWORKER',
        'REAL_REDIS_1_3_5_10_ATOMIC_CAP_AND_RELEASE_PASS',
    ):
        assert required in text
    for forbidden in ('"--publish"', '"--privileged"', "NEO4J_PASSWORD",
                      "/nas/", "PAPERCLIP_API_KEY"):
        assert forbidden not in text + client
    assert "for total in (1, 3, 5, 10)" in client
    assert "FLEET_MAX = 3" in client
    assert 'traceidx:{assistx-trace-index-v2}:active:fleet' in client
