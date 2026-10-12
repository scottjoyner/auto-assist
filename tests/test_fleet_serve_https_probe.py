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


def observation_response(*, state="fresh"):
    result = {
        "schema_version": 1,
        "source": "tailnet_observational_only",
        "authority": "not_admitted",
        "state": state,
        "observed_nodes": 1 if state == "fresh" else 0,
        "observed_endpoints": 1 if state == "fresh" else 0,
        "resident_endpoints": 1 if state == "fresh" else 0,
        "models": [{
            "display_name": "k2.gguf",
            "state": "resident_verified",
            "observed_instances": 1,
            "admitted": False,
            "selectable": False,
        }] if state == "fresh" else [],
        "truncated": False,
        "observation_sha256": "a" * 64 if state == "fresh" else None,
        "source_inventory_sha256": "b" * 64 if state == "fresh" else None,
    }
    return result


def _probe_observations(payload):
    def reader(path):
        if path.endswith("/api/v1/runtime/observations"):
            return 200, json.dumps(payload).encode()
        return fake_responses(path)
    return probe_self("x1-370.example.ts.net", 8443, transport=reader)


def test_fresh_observation_contract_is_nonselectable_and_not_admission():
    result = _probe_observations(observation_response())
    row = result["routes"]["observations"]
    assert row["observational_only_schema"] is True
    assert row["fresh_nonselectable_observations"] is True
    assert row["observations_state"] == "fresh"
    assert result["private_observation_source_bound"] is False
    assert result["trusted_reverse_proxy_header_boundary_verified"] is False
    assert result["acceptance"] == "HOLD"
    assert result["model_authorizations"] == result["model_requests"] == 0
    assert "k2.gguf" not in json.dumps(result)


def test_stale_response_may_have_valid_schema_but_is_not_fresh_evidence():
    result = _probe_observations(observation_response(state="stale"))
    row = result["routes"]["observations"]
    assert row["observational_only_schema"] is True
    assert row["fresh_nonselectable_observations"] is False
    assert row["observations_state"] == "stale"


@pytest.mark.parametrize("change", [
    lambda d: d["models"][0].update(selectable=True),
    lambda d: d["models"][0].update(admitted=True),
    lambda d: d["models"][0].update(display_name="100.64.4.10:1234"),
    lambda d: d["models"][0].update(display_name="node/private-model"),
    lambda d: d["models"][0].update(display_name="secret@node"),
    lambda d: d["models"][0].update(state="loaded_and_admitted"),
    lambda d: d["models"][0].update(observed_instances=0),
    lambda d: d["models"][0].update(connected_host="private-node"),
    lambda d: d.update(authority="admitted"),
    lambda d: d.update(schema_version=True),
    lambda d: d.update(observed_nodes=True),
    lambda d: d.update(observation_sha256=None),
    lambda d: d.update(source_inventory_sha256="private-host"),
    lambda d: d.update(models=[{}] * 81),
    lambda d: d.update(state="stale"),
])
def test_actionable_malformed_or_stale_with_models_is_not_valid_observation(change):
    payload = observation_response()
    change(payload)
    report = _probe_observations(payload)
    row = report["routes"]["observations"]
    assert row["http_status"] == 200
    assert row["observational_only_schema"] is False
    assert row["fresh_nonselectable_observations"] is False
    assert row["observations_state"] == "invalid_contract"
    assert report["model_authorizations"] == 0
    assert report["acceptance"] == "HOLD"


def test_non_fresh_state_with_models_cannot_claim_empty_sanitized_observation():
    payload = observation_response(state="unavailable")
    payload["models"] = observation_response()["models"]
    row = _probe_observations(payload)["routes"]["observations"]
    assert row["observational_only_schema"] is False
    assert row["observations_state"] == "invalid_contract"
