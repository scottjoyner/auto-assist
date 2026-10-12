"""Offline ingress inventory regressions; never inspect live credentials in CI."""
from __future__ import annotations

import json

import pytest

from assistx.fleet_ingress_boundary import assess_ingress_boundary

SYNTHETIC_SECRET = "synthetic-NEVER-LOG-SECRET-EXAMPLE"


def serve(mapped=False):
    return {
        "schema": "fleet-serve-route-preflight/v1",
        "explicit_routes_present_somewhere": {"observations": mapped},
        "acceptance": "HOLD",
    }


def container(
    *, strict="0", trusted="Tailscale-User-Login", allowlist="",
    bindings=None, extra=None,
):
    env = [
        f"ASSISTX_REQUIRE_BASIC_AUTH={strict}",
        f"TRUSTED_AUTH_HEADER={trusted}",
        "BASIC_AUTH_USER=synthetic-operator",
        f"BASIC_AUTH_PASS={SYNTHETIC_SECRET}",
        f"KIPNERTER_TAILNET_ALLOWED_LOGINS={allowlist}",
    ]
    env += extra or []
    return {
        "Name": "/assistx-api",
        "Config": {"Env": env},
        "NetworkSettings": {
            "Ports": {"8000/tcp": (
                [{"HostIp": "127.0.0.1", "HostPort": "8000"}]
                if bindings is None else bindings
            )},
        },
    }


def test_realistic_header_only_layout_is_hold_and_redacted():
    report = assess_ingress_boundary(container(), serve())
    assert report["trusted_identity_header_configured"] is True
    assert report["strict_basic_auth_configured"] is False
    assert report["basic_credentials_configured"] is True
    assert report["mobile_login_allowlist_configured"] is False
    assert report["api_8000_host_binding_loopback_only"] is True
    assert report["observation_route_explicitly_mapped"] is False
    assert "header_only_identity_precedes_basic" in report["reasons"]
    assert report["identity_header_provenance_verified"] is False
    assert report["docker_peer_bypass_ruled_out"] is False
    assert report["signed_gateway_receiver_wired_and_verified"] is False
    assert report["model_authorizations"] == report["model_requests"] == 0
    assert report["redacted"] is True and report["acceptance"] == "HOLD"
    serialized = json.dumps(report)
    for forbidden in (SYNTHETIC_SECRET, "synthetic-operator",
                      "Tailscale-User-Login", "127.0.0.1"):
        assert forbidden not in serialized


def test_strict_basic_and_allowlist_dont_establish_proxy_provenance():
    result = assess_ingress_boundary(
        container(strict="yes", allowlist="synthetic-operator@example.invalid"),
        serve(mapped=True),
    )
    assert result["strict_basic_auth_configured"] is True
    assert result["mobile_login_allowlist_configured"] is True
    assert result["observation_route_explicitly_mapped"] is True
    assert "header_only_identity_precedes_basic" not in result["reasons"]
    assert result["identity_header_provenance_verified"] is False
    assert result["acceptance"] == "HOLD"


@pytest.mark.parametrize("ip,port", [
    ("0.0.0.0", "8000"), ("::", "8000"),
    ("127.0.0.1", "8100"), ("100.64.1.1", "8000"),
])
def test_nonlocal_or_wrong_port_is_never_loopback_evidence(ip, port):
    doc = container(bindings=[{"HostIp": ip, "HostPort": port}])
    result = assess_ingress_boundary(doc, serve(mapped=True))
    assert result["api_8000_host_binding_loopback_only"] is False
    assert "api_host_binding_not_proven_loopback_only" in result["reasons"]
    assert result["acceptance"] == "HOLD"


def test_dual_stack_public_bind_cannot_hide_behind_loopback():
    doc = container(bindings=[
        {"HostIp": "127.0.0.1", "HostPort": "8000"},
        {"HostIp": "::", "HostPort": "8000"},
    ])
    assert not assess_ingress_boundary(doc, serve())["api_8000_host_binding_loopback_only"]


@pytest.mark.parametrize("corrupt", [
    lambda c: c.update(Name="/other"),
    lambda c: c["Config"].update(Env="not-a-list"),
    lambda c: c["Config"]["Env"].append("TRUSTED_AUTH_HEADER=duplicate"),
    lambda c: c["Config"]["Env"].append("malformed"),
    lambda c: c["NetworkSettings"].update(Ports=None),
])
def test_bad_or_ambiguous_metadata_is_rejected(corrupt):
    item = container()
    corrupt(item)
    with pytest.raises(ValueError):
        assess_ingress_boundary(item, serve())


def test_no_header_or_no_bind_is_not_false_identity_proof():
    doc = container(trusted="", bindings=[])
    row = assess_ingress_boundary(doc, serve(mapped=True))
    assert row["trusted_identity_header_configured"] is False
    assert row["api_8000_host_binding_loopback_only"] is False
    assert row["identity_header_provenance_verified"] is False
    assert row["acceptance"] == "HOLD"


def test_invalid_serve_evidence_rejected_before_env_projection():
    with pytest.raises(ValueError):
        assess_ingress_boundary(container(), {"schema": "untrusted"})


# Docker metadata is synthetic. Network IDs and peer IDs never enter reports.
def topology_container():
    row = container()
    row["Id"] = "a" * 64
    row["NetworkSettings"]["Networks"] = {
        "synthetic-private-A": {"NetworkID": "b" * 64},
        "synthetic-private-B": {"NetworkID": "c" * 64},
    }
    return row


def topology_network(network_id, peers, *, driver="bridge"):
    members = {"a" * 64: {"Name": "assistx-api", "IPv4Address": "172.24.0.5/16"}}
    members.update({
        peer: {"Name": f"synthetic-peer-{i}", "IPv4Address": "172.24.0.9/16"}
        for i, peer in enumerate(peers)
    })
    return {"Id": network_id, "Driver": driver, "Internal": False,
            "Containers": members}


def test_two_bridge_networks_report_peer_attachments_without_identities():
    from assistx.fleet_ingress_boundary import network_inspection_ids
    item = topology_container()
    networks = [
        topology_network("b" * 64, ["d" * 64, "e" * 64]),
        topology_network("c" * 64, ["d" * 64, "f" * 64]),
    ]
    assert network_inspection_ids(item) == ["b" * 64, "c" * 64]
    result = assess_ingress_boundary(item, serve(), network_inspections=networks)
    assert result["docker_network_inventory_complete"] is True
    assert result["docker_bridge_network_count"] == 2
    assert result["docker_bridge_peer_attachments"] == 4
    assert result["docker_unique_bridge_peer_count"] == 3
    assert result["docker_shared_bridge_peers_present"] is True
    assert result["docker_peer_bypass_ruled_out"] is False
    assert result["acceptance"] == "HOLD"
    assert "shared_docker_bridge_peers_attached" in result["reasons"]
    encoded = json.dumps(result)
    for forbidden in (SYNTHETIC_SECRET, "synthetic-private-A",
                      "172.24.0.9", "synthetic-peer", "d" * 64):
        assert forbidden not in encoded


def test_missing_topology_remains_explicitly_unknown():
    result = assess_ingress_boundary(topology_container(), serve())
    assert result["docker_network_inventory_complete"] is False
    assert result["docker_bridge_peer_attachments"] is None
    assert result["docker_shared_bridge_peers_present"] is None
    assert "docker_peer_topology_not_examined" in result["reasons"]
    assert result["acceptance"] == "HOLD"


def test_zero_bridge_peers_still_not_proof_of_safe_header_provenance():
    item = topology_container()
    networks = [topology_network("b" * 64, []),
                topology_network("c" * 64, [])]
    result = assess_ingress_boundary(item, serve(mapped=True),
                                     network_inspections=networks)
    assert result["docker_bridge_peer_attachments"] == 0
    assert result["docker_shared_bridge_peers_present"] is False
    assert result["docker_peer_bypass_ruled_out"] is False
    assert result["identity_header_provenance_verified"] is False
    assert result["acceptance"] == "HOLD"


@pytest.mark.parametrize("mutation", [
    lambda nets: nets.pop(),
    lambda nets: nets.append(nets[0]),
    lambda nets: nets[0].update(Id="f" * 64),
    lambda nets: nets[0].update(Containers=None),
    lambda nets: nets[0].get("Containers").pop("a" * 64),
    lambda nets: nets[0]["Containers"].update({"bad id": {}}),
])
def test_incomplete_unbound_or_malformed_network_inventory_fails_closed(mutation):
    item = topology_container()
    networks = [topology_network("b" * 64, ["d" * 64]),
                topology_network("c" * 64, ["e" * 64])]
    mutation(networks)
    with pytest.raises(ValueError):
        assess_ingress_boundary(item, serve(), network_inspections=networks)


def test_non_bridge_network_is_not_falsely_reported_as_bridge_peer():
    item = topology_container()
    networks = [topology_network("b" * 64, ["d" * 64], driver="host"),
                topology_network("c" * 64, [], driver="bridge")]
    row = assess_ingress_boundary(item, serve(), network_inspections=networks)
    assert row["docker_bridge_network_count"] == 1
    assert row["docker_bridge_peer_attachments"] == 0
    assert row["acceptance"] == "HOLD"


def test_network_id_must_be_canonical_and_nonduplicate():
    from assistx.fleet_ingress_boundary import network_inspection_ids
    item = topology_container()
    item["NetworkSettings"]["Networks"]["synthetic-private-B"]["NetworkID"] = "invalid"
    with pytest.raises(ValueError):
        network_inspection_ids(item)
    item["NetworkSettings"]["Networks"]["synthetic-private-B"]["NetworkID"] = "b" * 64
    with pytest.raises(ValueError):
        network_inspection_ids(item)
