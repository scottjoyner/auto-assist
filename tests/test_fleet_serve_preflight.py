"""Tailscale Serve route gate preserves transport and authentication boundaries."""
import json

import pytest

from assistx.fleet_serve_preflight import assess_serve_configuration

SELF = "x1-370.corp-tailnet.ts.net"
ROUTES = {
    "/api/v1/auth/whoami": {"Proxy": "http://127.0.0.1:8000"},
    "/api/v1/runtime/catalog": {"Proxy": "http://127.0.0.1:8000"},
}


def status(host=SELF):
    return {"Self": {"DNSName": host + "."}}


def serve(*, handlers=None, other=None):
    return {
        "Web": {
            SELF + ":443": {
                "Handlers": handlers if handlers is not None else ROUTES,
            },
            **(other or {}),
        },
    }


def test_explicit_observation_handler_missing_denies_admission():
    doc = assess_serve_configuration(serve(), status())
    assert doc["acceptance"] == "HOLD"
    assert doc["matching_https_listener_configs"] == 1
    assert doc["explicit_routes_present_somewhere"]["identity"] is True
    assert doc["explicit_routes_present_somewhere"]["catalog"] is True
    assert doc["explicit_routes_present_somewhere"]["observations"] is False
    assert doc["missing_explicit_routes"] == ["/api/v1/runtime/observations"]
    assert doc["authenticated_transport_verified"] is False
    assert doc["backend_http_verified"] is False
    assert doc["production_admitted"] is False
    assert doc["router_mutations"] == doc["provider_requests"] == 0
    assert SELF not in json.dumps(doc)
    assert "127.0.0.1" not in json.dumps(doc)


def test_root_proxy_is_ambiguous_not_proof_of_backend_404():
    doc = assess_serve_configuration(
        serve(handlers={**ROUTES, "/": {"Proxy": "http://127.0.0.1:8000"}}),
        status(),
    )
    assert doc["root_catchall_present"] is True
    assert doc["route_gap_assessment"] == "implicit_proxy_possible_live_check_required"
    assert doc["acceptance"] == "HOLD"


def test_all_explicit_routes_are_still_not_auth_evidence():
    routes = {
        **ROUTES, "/api/v1/runtime/observations": {"Proxy": "http://127.0.0.1:8000"},
    }
    doc = assess_serve_configuration(serve(handlers=routes), status())
    assert doc["explicit_routes_complete_on_one_listener"] is True
    assert doc["route_gap_assessment"] == "explicit_routes_complete_unverified_live"
    assert doc["authenticated_transport_verified"] is False
    assert doc["source_witness_bound"] is False
    assert doc["model_dispatch_allowed"] is False

def test_independent_host_routes_cannot_be_combined():
    other = {
        "another.ts.net:443": {
            "Handlers": {
                "/api/v1/runtime/observations": {"Proxy": "http://127.0.0.1:8000"},
            },
        },
    }
    doc = assess_serve_configuration(serve(other=other), status())
    assert doc["explicit_routes_complete_on_one_listener"] is False
    assert doc["explicit_routes_present_somewhere"]["observations"] is False


def test_no_serve_web_fails_closed():
    doc = assess_serve_configuration({"Web": {}}, status())
    assert doc["matching_https_listener_configs"] == 0
    assert doc["acceptance"] == "HOLD"
    assert len(doc["missing_explicit_routes"]) == 3


@pytest.mark.parametrize("bad_dns", ["not-tailnet.local", "", "x1.ts.net.example.com"])
def test_local_identity_not_ts_net_is_rejected(bad_dns):
    with pytest.raises(ValueError):
        assess_serve_configuration(serve(), status(bad_dns))


def test_non_default_https_port_is_recognized():
    obj = {"Web": {SELF + ":8443": {"Handlers": {**ROUTES}}}}
    doc = assess_serve_configuration(obj, status())
    assert doc["matching_https_listener_configs"] == 1
    assert doc["configured_https_ports"] == [8443]
    assert doc["identity_route_https_ports"] == [8443]
    assert doc["explicit_routes_present_somewhere"]["identity"] is True
    assert doc["explicit_routes_present_somewhere"]["observations"] is False


def test_distinct_listener_ports_do_not_combine_into_one_ready_route():
    doc = assess_serve_configuration({
        "Web": {
            SELF + ":8443": {"Handlers": ROUTES},
            SELF + ":8445": {"Handlers": {
                "/api/v1/runtime/observations": {"Proxy": "http://127.0.0.1:8000"},
            }},
        },
    }, status())
    assert doc["configured_https_ports"] == [8443, 8445]
    assert doc["identity_route_https_ports"] == [8443]
    assert doc["explicit_routes_complete_on_one_listener"] is False
    assert doc["explicit_routes_present_somewhere"]["observations"] is True
    assert doc["acceptance"] == "HOLD"


def test_malformed_local_tailnet_status_fails_closed():
    with pytest.raises(ValueError):
        assess_serve_configuration(serve(), {"Self": "untrusted"})
