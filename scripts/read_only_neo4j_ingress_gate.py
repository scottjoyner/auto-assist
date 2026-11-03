#!/usr/bin/env python3
"""Image-pinned, non-mutating Docker Neo4j ingress mismatch census.

Explicit research HOLD for the running container named neo4j, versus the
checked-in reconciliation overlay's loopback-only two-port, single-network
design. Does NOT inspect host firewall rules, test WAN reachability,
attest credentials or authorize applying an overlay to a live database.
No untrusted shell input, Compose changes or Docker mutations.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import subprocess
import sys
from typing import Any


TARGET = "neo4j"
EXPECTED_OVERLAY_NAME = "/assistx-production-neo4j"
EXPECTED_PORTS = frozenset(("7474/tcp", "7687/tcp"))
EXPECTED_NETWORK_COUNT = 1
IMAGE_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")


class IngressHold(RuntimeError):
    """No trustworthy image-pinned, read-only ingress proof is available."""


def _docker_inspect() -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["docker", "container", "inspect", TARGET],
            timeout=8, capture_output=True, text=True, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IngressHold("docker_inspect_unavailable") from exc
    if proc.returncode != 0 or len(proc.stdout) > 256000:
        raise IngressHold("docker_inspect_failed_or_oversized")
    try:
        rows = json.loads(proc.stdout)
    except (ValueError, TypeError) as exc:
        raise IngressHold("invalid_docker_inspect_json") from exc
    if not isinstance(rows, list) or len(rows) != 1:
        raise IngressHold("ambiguous_docker_container")
    row = rows[0]
    if not isinstance(row, dict):
        raise IngressHold("invalid_docker_container")
    return row


def _identity(obj: dict[str, Any], expected_image: str) -> tuple[str, str]:
    state = obj.get("State") or {}
    if (obj.get("Name") != "/" + TARGET
        or state.get("Running") is not True
        or obj.get("Image") != expected_image):
        raise IngressHold("runtime_identity_or_image_changed")
    cid = obj.get("Id")
    started = state.get("StartedAt")
    if not (isinstance(cid, str) and re.fullmatch(r"[a-f0-9]{64}", cid)
            and isinstance(started, str) and started.strip()):
        raise IngressHold("invalid_container_identity")
    return cid, started


def _scope(host: str) -> str:
    if host in ("0.0.0.0", "::", ""):
        return "wildcard"
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        return "unrecognized"
    return "loopback" if parsed.is_loopback else "nonloopback"


def _published(obj: dict[str, Any], area: str) -> dict[str, list[tuple[str, str]]]:
    src = obj.get(area) or {}
    bind = src.get("PortBindings" if area == "HostConfig" else "Ports")
    if not isinstance(bind, dict):
        raise IngressHold("published_port_map_missing_or_untrusted")
    result: dict[str, list[tuple[str, str]]] = {}
    for key, bindings in bind.items():
        if not isinstance(key, str) or not isinstance(bindings, list):
            if area == "NetworkSettings" and bindings is None:
                continue
            raise IngressHold("published_port_shape_untrusted")
        rows = []
        for item in bindings:
            if not isinstance(item, dict):
                raise IngressHold("published_binding_shape_untrusted")
            ip, port = item.get("HostIp"), item.get("HostPort")
            if not isinstance(ip, str) or not isinstance(port, str):
                raise IngressHold("invalid_host_binding")
            if not port.isdecimal() or not 1 <= int(port) <= 65535:
                raise IngressHold("invalid_host_port")
            rows.append((ip, port))
        if rows:
            result[key] = sorted(rows)
    return result


def assess(snapshot: dict[str, Any], expected_image: str) -> dict[str, Any]:
    _identity(snapshot, expected_image)
    host = _published(snapshot, "HostConfig")
    running = _published(snapshot, "NetworkSettings")
    networks = (snapshot.get("NetworkSettings") or {}).get("Networks")
    if not isinstance(networks, dict) or len(networks) > 32:
        raise IngressHold("network_attachment_map_untrusted")
    labels = (snapshot.get("Config") or {}).get("Labels") or {}
    if not isinstance(labels, dict):
        raise IngressHold("compose_provenance_untrusted")
    project = labels.get("com.docker.compose.project")
    service = labels.get("com.docker.compose.service")
    if project is not None and not isinstance(project, str):
        raise IngressHold("compose_provenance_untrusted")
    if service is not None and not isinstance(service, str):
        raise IngressHold("compose_provenance_untrusted")

    pairs = [(p, _scope(hostip)) for p, bindings in host.items()
             for hostip, _hostport in bindings]
    off_loopback = sum(scope != "loopback" for _, scope in pairs)
    actual = set(host)
    no_extra_missing = (actual == EXPECTED_PORTS
                        and all(len(host[p]) == 1 for p in EXPECTED_PORTS if p in host))
    network_count = len(networks)
    runtime_mismatch = host != running
    name_match = snapshot.get("Name") == EXPECTED_OVERLAY_NAME
    reasons = []
    if not no_extra_missing:
        reasons.append("publication_ports_do_not_match_expected_two_single_bindings")
    if off_loopback:
        reasons.append("wildcard_or_nonloopback_graph_publication")
    if runtime_mismatch:
        reasons.append("published_vs_runtime_binding_discrepancy")
    if network_count != EXPECTED_NETWORK_COUNT:
        reasons.append("unexpected_number_of_network_attachments")
    if not name_match:
        reasons.append("reconciled_production_container_identity_not_active")
    if service != "neo4j" or not project:
        reasons.append("compose_provenance_unverified")
    reasons.append("host_and_upstream_firewall_reachability_unverified")
    return {
        "production_decision": "HOLD",
        "change_authorized": False,
        "dispatch_authorized": False,
        "evidence_type": "DOCKER_IMAGE_PINNED_READ_ONLY_RUNTIME",
        "expected_image_verified": True,
        "container_running": True,
        "expected_loopback_port_publication": no_extra_missing and off_loopback == 0 and not runtime_mismatch,
        "published_expected_port_count": len(actual & EXPECTED_PORTS),
        "extra_published_port_count": len(actual - EXPECTED_PORTS),
        "off_loopback_publication_count": off_loopback,
        "published_runtime_binding_mismatch": runtime_mismatch,
        "network_attachment_count": network_count,
        "reconciled_container_name_active": name_match,
        "compose_provenance_present": bool(project and service == "neo4j"),
        "risk_flags": reasons,
        "external_reachability_verified": False,
        "physical_firewall_policy_verified": False,
        "no_mutations": True,
    }


def collect(expected_image: str) -> dict[str, Any]:
    if not isinstance(expected_image, str) or not IMAGE_SHA.fullmatch(expected_image):
        raise IngressHold("immutable_sha256_image_pin_required")
    before = _docker_inspect()
    identity = _identity(before, expected_image)
    result = assess(before, expected_image)
    after = _docker_inspect()
    if _identity(after, expected_image) != identity:
        raise IngressHold("container_changed_during_readback")
    if (_published(before, "HostConfig") != _published(after, "HostConfig")
        or _published(before, "NetworkSettings") != _published(after, "NetworkSettings")
        or set((before.get("NetworkSettings") or {}).get("Networks") or {}) !=
           set((after.get("NetworkSettings") or {}).get("Networks") or {})):
        raise IngressHold("network_binding_drift_during_readback")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--json", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        result = collect(args.expected_image)
    except IngressHold:
        print(json.dumps({
            "production_decision": "HOLD", "change_authorized": False,
            "dispatch_authorized": False,
            "error_type": "image_pinned_ingress_census_unverified",
        }, sort_keys=True))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 3


if __name__ == "__main__":
    sys.exit(main())
