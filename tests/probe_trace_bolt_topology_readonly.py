"""Read-only research network isolation gate for AssistX ↔ Neo4j.

Evidence is TOPOLOGY and TCP connectivity only; a successful TCP handshake
does NOT prove credentials or permission to execute Cypher. This script never
performs authentication, Bolt protocol messages, queries, Docker mutations,
network/firewall changes, or publication of secret environment variables.

No Docker calls unless ASSISTX_BOUNDARY_READONLY=1. Optional socket-only
container probe requires *another* ASSISTX_BOUNDARY_TCP_PROBE=1.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping

WATCH = ("neo4j", "assistx-worker", "assistx-api")
BOLT = "7687/tcp"
NEO4J_ENV_NAMES = (
    "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE"
)


def published_on_all_interfaces(value: str | None) -> bool:
    # Docker HostIp of "" means wildcard, same as 0.0.0.0.
    return value in ("", "0.0.0.0", "::", "[::]")


def analyze_docker_inspect(
    containers: Mapping[str, Mapping[str, Any]],
    tcp_results: Mapping[str, bool] | None = None
) -> dict[str, Any]:
    if any(k not in containers for k in WATCH):
        return {"verdict": "INCOMPLETE_FAIL_CLOSED",
                "missing": [k for k in WATCH if k not in containers]}
    neo = containers["neo4j"]
    graph_nets = set(neo.get("NetworkSettings", {}).get("Networks", {}))
    binds = neo.get("HostConfig", {}).get("PortBindings") or {}
    bolt_bindings = binds.get(BOLT) or []
    exposed_bolt = [
        b for b in bolt_bindings
        if published_on_all_interfaces(b.get("HostIp"))
    ]
    observed = tcp_results or {}
    result: dict[str, Any] = {
        "verdict": "ISOLATION_UNPROVEN",
        "worker_common_graph_networks": {},
        "worker_direct_bolt_tcp": {},
        "graph_client_env_keys_present": {},
        "neo4j_bolt_host_wildcard": bool(exposed_bolt),
        "bolt_host_published": bool(bolt_bindings),
        "graph_network_count": len(graph_nets),
        "physical_graph_write_authorization_tested": False,
        "credentials_or_cypher_tested": False,
        "production_safe_to_promote": False,
    }
    for actor in ("assistx-worker", "assistx-api"):
        actor_nets = set(
            containers[actor].get("NetworkSettings", {}).get("Networks", {}))
        result["worker_common_graph_networks"][actor] = bool(
            graph_nets & actor_nets)
        result["worker_direct_bolt_tcp"][actor] = observed.get(actor)
        # Presence of configuration names is NOT a secret read, and does
        # not establish whether a credential is valid or its Neo4j role.
        result["graph_client_env_keys_present"][actor] = (
            containers[actor].get("Neo4jCredentialConfig") or {})
    if exposed_bolt or any(result["worker_common_graph_networks"].values()
                            ) or any(v is True for v in observed.values()):
        result["verdict"] = "NETWORK_BYPASS_PRESENT_NO_GO"
    return result


def _read_inspect(name: str) -> dict:
    p = subprocess.run(
        ["docker", "inspect", name], capture_output=True,
        text=True, timeout=9, check=True)
    data = json.loads(p.stdout)
    if len(data) != 1:
        raise ValueError("REQUIRE_EXACT_DOCKER_CONTAINER")
    # Only project environment variable NAMES to booleans. Never write
    # secret values, even to local console logs or GitHub evidence.
    env_keys = {
        entry.partition("=")[0]
        for entry in data[0].get("Config", {}).get("Env", [])
    }
    return {
        "NetworkSettings": {
            "Networks": {key: {} for key in
                         data[0].get("NetworkSettings", {}).get(
                             "Networks", {})}
        },
        "HostConfig": {
            "PortBindings": data[0].get("HostConfig", {}).get(
                "PortBindings") or {}
        },
        "Neo4jCredentialConfig": {
            name: (name in env_keys) for name in NEO4J_ENV_NAMES
        }
    }


def _tcp_probe_container(name: str) -> bool:
    """One TCP connect only; no authentication, query or client credentials."""
    script = (
        "import socket,sys;"
        "s=socket.create_connection(('neo4j',7687),timeout=1.5);"
        "s.close();sys.stdout.write('TCP_CONNECTED')"
    )
    p = subprocess.run(
        ["docker", "exec", name, "python", "-c", script],
        capture_output=True, text=True, timeout=8, check=False)
    return p.returncode == 0 and p.stdout.strip() == "TCP_CONNECTED"


def main() -> int:
    if os.getenv("ASSISTX_BOUNDARY_READONLY") != "1":
        raise SystemExit("EXPLICIT_BOUNDARY_READONLY_OPT_IN_REQUIRED")
    try:
        containers = {name: _read_inspect(name) for name in WATCH}
        results = {}
        if os.getenv("ASSISTX_BOUNDARY_TCP_PROBE") == "1":
            results = {
                name: _tcp_probe_container(name)
                for name in ("assistx-worker", "assistx-api")
            }
        outcome = analyze_docker_inspect(containers, results)
    except (subprocess.SubprocessError, OSError, ValueError, KeyError):
        outcome = {"verdict": "INCOMPLETE_FAIL_CLOSED"}
    print(json.dumps(outcome, sort_keys=True))
    return 0 if outcome["verdict"] == "ISOLATION_UNPROVEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
