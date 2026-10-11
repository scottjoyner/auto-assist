"""Offline failure-mode evidence for current Neo4j and mobile auth topology.

No production logins, Neo4j credentials, TCP packets, or Docker calls.
The synthetic allowlisted header uses an example.invalid login only and
demonstrates the current *code-level conditional trust assumption*.
"""
from __future__ import annotations

import ast
from email.header import decode_header, make_header
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Request, HTTPException

from probe_trace_bolt_topology_readonly import (
    analyze_docker_inspect, published_on_all_interfaces,
)


def node(networks, bindings=None):
    return {
        "NetworkSettings": {"Networks": {k: {} for k in networks}},
        "HostConfig": {"PortBindings": bindings or {}},
        "Config": {"Env": ["RESEARCH_SECRET_MUST_NOT_LEAK=example"]},
    }


def test_shared_worker_neo4j_network_proves_nonisolation_but_not_cypher():
    topology = {
        "neo4j": node(["neo_private", "worker_bridge"]),
        "assistx-worker": node(["worker_bridge"]),
        "assistx-api": node(["api_bridge"]),
    }
    result = analyze_docker_inspect(topology)
    assert result["verdict"] == "NETWORK_BYPASS_PRESENT_NO_GO"
    assert result["worker_common_graph_networks"] == {
        "assistx-worker": True, "assistx-api": False
    }
    assert result["physical_graph_write_authorization_tested"] is False
    assert result["credentials_or_cypher_tested"] is False
    assert "RESEARCH_SECRET_MUST_NOT_LEAK" not in json.dumps(result)


@pytest.mark.parametrize("host_ip", ["", "0.0.0.0", "::", "[::]"])
def test_wildcard_bolt_host_binding_blocks_promotion(host_ip):
    topology = {
        "neo4j": node(["private"], {
            "7687/tcp": [{"HostIp": host_ip, "HostPort": "7688"}]}),
        "assistx-worker": node(["worker"]),
        "assistx-api": node(["api"]),
    }
    result = analyze_docker_inspect(topology)
    assert published_on_all_interfaces(host_ip)
    assert result["neo4j_bolt_host_wildcard"] is True
    assert result["verdict"] == "NETWORK_BYPASS_PRESENT_NO_GO"


def test_connection_only_evidence_is_not_a_successful_graph_query():
    topology = {
        "neo4j": node(["private"]),
        "assistx-worker": node(["worker"]),
        "assistx-api": node(["api"]),
    }
    result = analyze_docker_inspect(
        topology, {"assistx-worker": True, "assistx-api": False})
    assert result["worker_direct_bolt_tcp"]["assistx-worker"] is True
    assert result["verdict"] == "NETWORK_BYPASS_PRESENT_NO_GO"
    assert result["credentials_or_cypher_tested"] is False


def test_missing_inventory_fails_closed_and_no_promotion_claim():
    result = analyze_docker_inspect({"neo4j": node(["graph"])})
    assert result["verdict"] == "INCOMPLETE_FAIL_CLOSED"


def test_unknown_connectivity_is_not_admission_proof():
    topology = {
        "neo4j": node(["private"]),
        "assistx-worker": node(["worker"]),
        "assistx-api": node(["api"]),
    }
    result = analyze_docker_inspect(topology)
    assert result["verdict"] == "ISOLATION_UNPROVEN"
    assert not result["production_safe_to_promote"]


def test_probe_is_manual_readonly_and_has_no_mutating_docker_commands():
    script = Path(__file__).with_name(
        "probe_trace_bolt_topology_readonly.py").read_text()
    assert "EXPLICIT_BOUNDARY_READONLY_OPT_IN_REQUIRED" in script
    assert 'ASSISTX_BOUNDARY_TCP_PROBE' in script
    assert '["docker", "inspect", name]' in script
    assert '["docker", "exec", name, "python", "-c", script]' in script
    for dangerous in ('"docker", "run"', '"docker", "network", "disconnect"',
                      '"docker", "stop"', '"docker", "restart"',
                      '"docker", "update"', '"docker", "rm"'):
        assert dangerous not in script


def _source_tailnet_helper():
    """Load the exact four checked-in source functions without package init.

    Hosted research CI intentionally does not install the entire AssistX
    production dependency graph or open any sockets.
    """
    source = Path(__file__).resolve().parents[1] / (
        "src/assistx/mobile_agent_routes.py")
    tree = ast.parse(source.read_text())
    wanted = {
        "_decode_identity_header", "_trusted_login_header_name",
        "_allowed_tailnet_login", "_tailnet_identity",
    }
    nodes = [
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in wanted
    ]
    assert {n.name for n in nodes} == wanted
    scope = {
        "os": os, "HTTPException": HTTPException, "Request": Request,
        "_TAILSCALE_LOGIN_HEADER": "Tailscale-User-Login",
        "decode_header": decode_header, "make_header": make_header,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]),
                 str(source), "exec"), scope)
    return SimpleNamespace(**{
        name: scope[name] for name in wanted
    })


def test_synthetic_allowed_header_current_helper_has_no_peer_provenance(monkeypatch):
    """COUNTEREXAMPLE to a claim of code-enforced proxy identity provenance.

    A synthetic, unverified request from TEST-NET address is accepted by
    the helper IF env enables canonical-header auth and its allowlist
    contains that synthetic identity. This is NOT a production login test.
    """
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS",
                       "synthetic-boundary@example.invalid")
    mod = _source_tailnet_helper()
    scope = {
        "type": "http", "method": "GET",
        "scheme": "http", "http_version": "1.1",
        "path": "/api/v1/auth/whoami",
        "headers": [
            (b"tailscale-user-login", b"synthetic-boundary@example.invalid"),
            (b"tailscale-user-name", b"Forged Identity"),
        ],
        "client": ("198.51.100.42", 52342),
        "server": ("127.0.0.1", 8000),
    }
    untrusted_request = Request(scope)
    assert mod._tailnet_identity(untrusted_request) == (
        "synthetic-boundary@example.invalid", "Forged Identity")
    # This is a documented negative finding, NOT a secure implementation.


def test_noncanonical_header_configuration_disables_identity_trust(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "X-Forwarded-User")
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS",
                       "synthetic-boundary@example.invalid")
    src = Path(__file__).resolve().parents[1] / "src"
    monkeypatch.syspath_prepend(str(src))
    mod = importlib.import_module("assistx.mobile_agent_routes")
    request = Request({
        "type": "http", "method": "GET", "path": "/test",
        "headers": [(b"tailscale-user-login",
                     b"synthetic-boundary@example.invalid")],
        "client": ("198.51.100.42", 10000),
    })
    assert mod._tailnet_identity(request) is None
