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
