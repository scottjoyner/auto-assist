"""Read-only HTTPS self-probe is never a router admission decision."""
import hashlib
import json

import pytest

from assistx.fleet_serve_https_probe import probe_self


def identity():
    login = "fake-local-user@example.test"
    return {
        "authenticated": True,
        "provider": "tailscale",
        "login": login,
        "account_id": "tailscale:" + hashlib.sha256(login.encode()).hexdigest()[:24],
    }


def fake_responses(path):
    if path.endswith("/api/v1/auth/whoami"):
        return 200, json.dumps(identity()).encode()
    if path.endswith("/api/v1/runtime/catalog"):
        return 200, json.dumps({
            "schema_version": "2", "models": [], "runtimes": [],
        }).encode()
    if path.endswith("/api/v1/runtime/observations"):
        return 404, None
    raise AssertionError("unexpected request")


def test_mocked_gets_do_not_qualify_real_transport_or_models():
    result = probe_self("x1-370.example.ts.net", 8443, transport=fake_responses)
    assert result["https_listener_port"] == 8443
    assert result["transport_test_double"] is True
    assert result["system_tls_validation_attempted"] is False
    assert result["routes"]["identity"]["http_status"] == 200
    assert result["routes"]["identity"]["tailscale_whoami_contract_valid"] is True
    assert result["routes"]["catalog"]["catalog_schema_v2"] is True
    assert result["routes"]["observations"]["http_status"] == 404
    assert result["gateway_sso_claim_verifiable"] is False
    assert result["trusted_reverse_proxy_header_boundary_verified"] is False
    assert result["private_observation_source_bound"] is False
    assert result["model_authorizations"] == result["model_requests"] == 0
    assert result["acceptance"] == "HOLD"
    assert "fake-local-user" not in json.dumps(result)


def test_transport_failure_is_explicit_not_a_404():
    def refused(url):
        raise ConnectionRefusedError(111, "connection refused")
    result = probe_self("x1-370.example.ts.net", 8443, transport=refused)
    assert all(row["transport_state"] == "error" for row in result["routes"].values())
    assert all(row["http_status"] is None for row in result["routes"].values())


def test_legacy_basic_whoami_is_not_sso():
    def legacy(path):
        if path.endswith("/api/v1/auth/whoami"):
            return 200, json.dumps({
                "authenticated": False, "provider": "legacy", "login": "root"
            }).encode()
        return 401, None
    result = probe_self("x1-370.example.ts.net", 8443, transport=legacy)
    assert result["routes"]["identity"]["tailscale_whoami_contract_valid"] is False
    assert result["acceptance"] == "HOLD"
def test_malformed_and_wrong_schema_do_not_claim_success():
    def invalid(path):
        return 200, b"not-json"
    report = probe_self("x1-370.example.ts.net", 8443, transport=invalid)
    assert all(row["transport_state"] == "http_response" for row in report["routes"].values())
    assert all(row["response_contract_invalid"] is True for row in report["routes"].values())
    assert report["routes"]["identity"]["tailscale_whoami_contract_valid"] is False
    assert report["routes"]["catalog"]["catalog_schema_v2"] is False
    assert report["model_authorizations"] == 0


@pytest.mark.parametrize("hostname,port", [
    ("127.0.0.1", 8443),
    ("x1-370.example.ts.net", 0),
    ("x1-370.example.ts.net", -1),
    ("x1-370.example.ts.net", "8443"),
    ("fake.ts.net:8443", 8443),
    ("fake.ts.net/anything", 8443),
])
def test_destination_restriction(hostname, port):
    with pytest.raises(ValueError):
        probe_self(hostname, port, transport=fake_responses)
