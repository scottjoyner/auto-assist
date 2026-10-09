#!/usr/bin/env python3
"""Read-only ingress audit: no requests, credentials, header injection or writes.

Inspect AssistX backend's configured auth mode and Docker networks. A passing
synthetic test does not prove that production reverse proxies attest identity.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any


def _env(names: list[str]) -> dict[str, str]:
    if not isinstance(names, list) or any(
        not isinstance(value, str) or "=" not in value for value in names
    ):
        raise ValueError("invalid environment metadata")
    return {value.partition("=")[0]: value.partition("=")[2] for value in names}


def assess(api: dict[str, Any], networks: list[dict[str, Any]]) -> dict[str, object]:
    if api.get("Name") != "/assistx-api":
        raise ValueError("unexpected backend identity")
    config = api.get("Config")
    host = api.get("HostConfig")
    net = api.get("NetworkSettings")
    if not isinstance(config, dict) or not isinstance(host, dict) or not isinstance(net, dict):
        raise ValueError("missing container inspection fields")
    values = _env(config.get("Env"))
    strict = values.get("ASSISTX_REQUIRE_BASIC_AUTH", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }
    header_only_possible = bool(values.get("TRUSTED_AUTH_HEADER")) and not strict
    published = host.get("PortBindings") or {}
    bound = published.get("8000/tcp")
    loopback = bool(isinstance(bound, list) and bound and all(
        item.get("HostIp") in {"127.0.0.1", "::1"}
        for item in bound if isinstance(item, dict)
    ) and len(bound) == len([item for item in bound if isinstance(item, dict)]))
    found = net.get("Networks") or {}
    if not isinstance(found, dict) or not found:
        raise ValueError("network identity unavailable")
    expected = set(found)
    observed: set[str] = set()
    peers: set[str] = set()
    for network in networks:
        name = network.get("Name")
        if name not in expected or name in observed:
            raise ValueError("unapproved network or duplicated result")
        observed.add(name)
        members = network.get("Containers") or {}
        if not isinstance(members, dict):
            raise ValueError("invalid peer inventory")
        for member in members.values():
            member_name = member.get("Name") if isinstance(member, dict) else None
            if not isinstance(member_name, str):
                raise ValueError("invalid peer name")
            if member_name != "assistx-api":
                peers.add(member_name)
    if observed != expected:
        raise ValueError("incomplete network coverage")
    # A loopback-only *host* publish is not an isolation guarantee across
    # Docker bridge peers, which can connect to a container's listening port.
    exposed = header_only_possible and bool(peers)
    return {
        "schema_version": "assistx.rc2.ingress-peer-gate.v1",
        "backend_container_running": api.get("State", {}).get("Running") is True,
        "host_publish_loopback_only": loopback,
        "trusted_header_mode_active": header_only_possible,
        "strict_basic_mode_active": strict,
        "shared_network_count": len(networks),
        "distinct_peer_container_count": len(peers),
        "header_trust_alternate_path": (
            "EXPOSED_POSSIBLE" if exposed else "NOT_DEMONSTRATED"
        ),
        "ingress_identity_provenance": "UNVERIFIED",
        "production_release_authorized": False,
    }


def main() -> int:
    try:
        p = subprocess.run(
            ["docker", "inspect", "--type", "container", "assistx-api"],
            capture_output=True, timeout=10, check=False,
        )
        if p.returncode:
            raise ValueError("Docker container inspection unavailable")
        data = json.loads(p.stdout)
        if not isinstance(data, list) or len(data) != 1:
            raise ValueError("unexpected inspect result")
        api = data[0]
        names = list((api.get("NetworkSettings") or {}).get("Networks") or {})
        if len(names) > 8 or not names:
            raise ValueError("unbounded or missing network list")
        q = subprocess.run(
            ["docker", "network", "inspect", *names],
            capture_output=True, timeout=10, check=False,
        )
        if q.returncode:
            raise ValueError("network inspection unavailable")
        report = assess(api, json.loads(q.stdout))
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        report = {
            "schema_version": "assistx.rc2.ingress-peer-gate.v1",
            "header_trust_alternate_path": "UNKNOWN",
            "ingress_identity_provenance": "UNVERIFIED",
            "production_release_authorized": False,
        }
    print(json.dumps(report, sort_keys=True))
    return 2  # Never confer backend trust or release authority.


if __name__ == "__main__":
    sys.exit(main())
