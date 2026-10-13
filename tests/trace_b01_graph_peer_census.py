"""Read-only Docker network/port census for AssistX B01 graph bypass.

Manual opt-in. Only docker ps + narrowly formatted docker inspect are called:
never requests container environment, credentials, or graph transactions.
This is a reachability risk inventory, NOT an authenticated-access test.
"""
from __future__ import annotations
import json
import os
import re
import subprocess
from typing import Mapping

GRAPH = "neo4j"
PORTS = ("7687/tcp", "7474/tcp")
EXPECTED_ACTORS = frozenset(("assistx-api", "assistx-worker"))
FMT = "{{json .Name}}<<<B01>>>{{json .NetworkSettings.Networks}}<<<B01>>>{{json .HostConfig.PortBindings}}"

class CensusDenied(ValueError):
    pass

def _run(*args: str) -> str:
    done = subprocess.run(["docker", *args], capture_output=True, text=True,
                          check=True, timeout=12)
    return done.stdout.strip()
def inspect_projection(name: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise CensusDenied("INVALID_CONTAINER_NAME")
    chunks = _run("inspect", "--format", FMT, name).split("<<<B01>>>")
    if len(chunks) != 3:
        raise CensusDenied("MALFORMED_FORMATTED_INSPECT")
    actual, networks, binds = (json.loads(part) for part in chunks)
    if actual != "/"+name or type(networks) is not dict:
        raise CensusDenied("INSPECT_IDENTITY_OR_NETWORK_UNVERIFIED")
    if binds is None:
        binds = {}
    if type(binds) is not dict:
        raise CensusDenied("INVALID_PORT_BINDINGS")
    return {"networks": sorted(networks), "ports": {
        p: [dict(HostIp=b.get("HostIp"), HostPort=b.get("HostPort"))
            for b in (binds.get(p) or [])] for p in PORTS}}

def collect() -> dict:
    if os.getenv("ASSISTX_B01_CENSUS_READONLY") != "1":
        raise CensusDenied("EXPLICIT_READONLY_OPT_IN_REQUIRED")
    names = _run("ps", "--format", "{{.Names}}").splitlines()
    if not names or len(names)>128 or len(set(names)) != len(names):
        raise CensusDenied("INCOMPLETE_OR_DUPLICATE_CONTAINER_LIST")
    return {name:inspect_projection(name) for name in names}
def analyze(containers: Mapping[str, dict]) -> dict:
    if GRAPH not in containers or not EXPECTED_ACTORS.issubset(containers):
        return {"verdict":"INCOMPLETE_FAIL_CLOSED",
                "release_authorized":False}
    neo = containers[GRAPH]
    networks = set(neo["networks"])
    if not networks:
        return {"verdict":"INCOMPLETE_FAIL_CLOSED",
                "release_authorized":False}
    peers = {}
    for name,entry in containers.items():
        if name != GRAPH:
            common = sorted(networks.intersection(entry["networks"]))
            if common:
                peers[name] = common
    ports = neo["ports"]
    published = {p:ports[p] for p in PORTS if ports[p]}
    wildcard = {p:any(b.get("HostIp") in ("","0.0.0.0","::","[::]")
                      for b in ports[p]) for p in PORTS}
    return {
        "schema":"assistx-b01-all-running-graph-peers-v1",
        "verdict":("NETWORK_BYPASS_PRESENT_NO_GO" if peers or published
                   else "ISOLATION_UNPROVEN"),
        "running_container_count":len(containers),
        "graph_network_count":len(networks),
        "potential_bypass_peers":peers,
        "potential_bypass_peer_count":len(peers),
        "published_graph_ports":published,
        "wildcard_bindings":wildcard,
        "graph_credentials_or_cypher_tested":False,
        "socket_reachability_for_all_peers_tested":False,
        "host_and_tailnet_firewall_trust_proven":False,
        "release_authorized":False,
    }

def main() -> int:
    try:
        result=analyze(collect())
    except (CensusDenied, subprocess.SubprocessError, OSError,
            ValueError, KeyError, TypeError):
        result={"verdict":"INCOMPLETE_FAIL_CLOSED","release_authorized":False}
    print(json.dumps(result,sort_keys=True))
    return 2 if result["verdict"] != "ISOLATION_UNPROVEN" else 0

if __name__=="__main__":
    raise SystemExit(main())