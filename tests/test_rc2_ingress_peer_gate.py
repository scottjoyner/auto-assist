"""Synthetic Docker ingress topology acceptance; no sockets or credential reads."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rc2-ingress-peer-gate.py"
spec = importlib.util.spec_from_file_location("rc2_ingress_peer_gate", SCRIPT)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def api(*, header="Tailscale-User-Login", strict="0"):
    return {
        "Name": "/assistx-api",
        "State": {"Running": True},
        "Config": {"Env": [
            "TRUSTED_AUTH_HEADER=" + header, "ASSISTX_REQUIRE_BASIC_AUTH=" + strict,
            "BASIC_AUTH_PASS=NEVER_OUTPUT_TEST_CREDENTIAL",
        ]},
        "HostConfig": {"PortBindings": {
            "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}],
        }},
        "NetworkSettings": {"Networks": {"synthetic-shared-bridge": {}}},
    }


def network(peers=None):
    peers = peers or ["assistx-api", "untrusted-synthetic-neighbor"]
    return [{"Name": "synthetic-shared-bridge", "Containers": {
        str(i): {"Name": name} for i, name in enumerate(peers)
    }}]


def test_loopback_publish_does_not_fence_docker_peer_header():
    result = mod.assess(api(), network())
    assert result["host_publish_loopback_only"] is True
    assert result["header_trust_alternate_path"] == "EXPOSED_POSSIBLE"
    assert result["trusted_header_mode_active"] is True
    assert result["production_release_authorized"] is False
    assert "NEVER_OUTPUT_TEST_CREDENTIAL" not in json.dumps(result)


def test_strict_basic_source_gate_blocks_header_only_trust():
    result = mod.assess(api(strict="1"), network())
    assert result["strict_basic_mode_active"] is True
    assert result["header_trust_alternate_path"] == "NOT_DEMONSTRATED"
    assert result["ingress_identity_provenance"] == "UNVERIFIED"
    assert result["production_release_authorized"] is False


def test_no_header_does_not_invent_exposure():
    result = mod.assess(api(header=""), network())
    assert result["header_trust_alternate_path"] == "NOT_DEMONSTRATED"


def test_empty_peers_is_not_operator_approval():
    result = mod.assess(api(), network(["assistx-api"]))
    assert result["distinct_peer_container_count"] == 0
    assert result["production_release_authorized"] is False


@pytest.mark.parametrize("networks", [
    [],
    [{"Name": "unapproved-bridge", "Containers": {}}],
    [{"Name": "synthetic-shared-bridge", "Containers": {"x": {"Name": None}}}],
    network() + network(),
])
def test_unknown_incomplete_or_duplicated_networks_denied(networks):
    with pytest.raises(ValueError):
        mod.assess(api(), networks)


def test_runtime_failure_fails_closed_without_leaking_docker_inspect(
    monkeypatch, capsys,
):
    class Failure:
        returncode = 1
        stdout = b'{"Config":{"Env":["BASIC_AUTH_PASS=SHOULD_NOT_APPEAR"]}}'

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: Failure())
    assert mod.main() == 2
    output = capsys.readouterr().out
    assert "SHOULD_NOT_APPEAR" not in output
    assert json.loads(output)["header_trust_alternate_path"] == "UNKNOWN"
