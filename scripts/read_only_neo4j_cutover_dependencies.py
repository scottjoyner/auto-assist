#!/usr/bin/env python3
"""Read-only, image-pinned Neo4j client/forwarder cutover dependency inventory.

No network connections, sudo, service restarts, Docker changes, secret output,
Task mutations, or production cutover. Every result is HOLD by design.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit


INGRESS = runpy.run_path(str(Path(__file__).resolve().parent / "read_only_neo4j_ingress_gate.py"))
SERVICES = ("assistx-api", "assistx-worker", "assistx-hermes-adapter", "phonelog")
FORWARDER = "assistx-neo4j-bolt-forward.service"
_GRAPH_PORT = 7688
_MAX_BYTES = 1_500_000


class PreflightHold(RuntimeError):
    """Required read-only topology evidence was missing or changed."""


def _command(args: list[str], *, limit: int = _MAX_BYTES) -> str:
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightHold("read_only_command_unavailable") from exc
    if p.returncode != 0 or len(p.stdout) > limit:
        # Never emit command stderr: Docker/systemctl may include paths.
        raise PreflightHold("read_only_command_failed_or_oversized")
    return p.stdout


def _scope(host: str) -> str:
    if host in ("0.0.0.0", "::", "*", ""):
        return "wildcard"
    if host in ("localhost",):
        return "loopback"
    try:
        return "loopback" if ipaddress.ip_address(host.strip("[]")).is_loopback else "nonloopback"
    except ValueError:
        return "unverified"


def _client_info(obj: dict[str, Any]) -> dict[str, Any] | None:
    env: dict[str, str] = {}
    for entry in (obj.get("Config") or {}).get("Env") or []:
        if not isinstance(entry, str):
            raise PreflightHold("client_environment_shape_unverified")
        if "=" in entry:
            k, v = entry.split("=", 1)
            env[k] = v
    raw = env.get("NEO4J_URI") or env.get("NEO4J_URL") or env.get("NEO4J_BOLT_URI")
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname or ""
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise PreflightHold("graph_client_endpoint_shape_unverified") from exc
    hostclass = (
        "docker_host_gateway" if host in ("host.docker.internal", "gateway.docker.internal")
        else "container_loopback" if _scope(host) == "loopback"
        else "docker_service_name" if host in ("neo4j", "assistx-neo4j", "assistx-production-neo4j")
        else "unverified_endpoint"
    )
    return {"network_mode": (obj.get("HostConfig") or {}).get("NetworkMode"),
            "endpoint_class": hostclass, "port": port,
            "graph_username_configured": bool(env.get("NEO4J_USER") or env.get("NEO4J_USERNAME"))}


def _clients(objs: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(objs, list) or len(objs) > 100:
        raise PreflightHold("running_container_inventory_unbounded")
    seen: set[str] = set()
    known: dict[str, dict[str, Any]] = {}
    unexpected = 0
    for obj in objs:
        if not isinstance(obj, dict):
            raise PreflightHold("running_container_inventory_malformed")
        name = obj.get("Name")
        if not isinstance(name, str) or not name.startswith("/") or name in seen:
            raise PreflightHold("duplicate_or_invalid_running_container")
        seen.add(name)
        client = _client_info(obj)
        if client is None:
            continue
        short = name.lstrip("/")
        if short in SERVICES:
            known[short] = client
        else:
            unexpected += 1
    expected_three = sum(
        name in known and known[name]["endpoint_class"] == "docker_host_gateway"
        and known[name]["port"] == _GRAPH_PORT
        for name in SERVICES[:3]
    )
    phone = known.get("phonelog") or {}
    phone_verified = (phone.get("network_mode") == "host"
                      and phone.get("endpoint_class") == "container_loopback"
                      and phone.get("port") == _GRAPH_PORT)
    return {"running_container_count": len(objs),
            "graph_client_config_count": len(known) + unexpected,
            "expected_assistx_gateway_clients": expected_three,
            "phonelog_host_network_loopback_configured": bool(phone_verified),
            "unknown_graph_client_count": unexpected,
            "missing_expected_clients": sum(s not in known for s in SERVICES),
            "all_expected_clients_have_username_config": all(
                known[s]["graph_username_configured"] for s in SERVICES if s in known),
            "source_client_runtime_relation_verified": False}


def _forward(unit: str, active: str, socket_text: str) -> dict[str, Any]:
    lines = [s.strip() for s in unit.splitlines() if s.startswith("ExecStart=")]
    if len(lines) != 1:
        raise PreflightHold("forwarder_command_missing_or_ambiguous")
    src = lines[0].split("=", 1)[1]
    tokens = src.split()
    listen = next((s for s in tokens if s.startswith("TCP-LISTEN:")), "")
    dest = next((s for s in tokens if s.startswith("TCP:")), "")
    m = re.match(r"TCP-LISTEN:(\d+)(.*)\Z", listen)
    n = re.match(r"TCP:([^:\s,]+):(\d+)(?:,.*)?\Z", dest)
    if not m or not n:
        raise PreflightHold("forwarder_socket_arguments_unverified")
    bound = re.search(r"(?:^|,)bind=([^,\s]+)", m.group(2))
    listen_scope = _scope(bound.group(1)) if bound else "unspecified"
    dest_scope = _scope(n.group(1))
    port = int(m.group(1))
    dest_port = int(n.group(2))
    listeners = []
    for line in socket_text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        hostport = parts[3]
        if ":" not in hostport:
            continue
        host, raw_port = hostport.rsplit(":", 1)
        if raw_port.isdecimal() and int(raw_port) == dest_port:
            listeners.append(_scope(host))
    return {"service_active": active.strip() == "active",
            "binary_is_socat": bool(re.search(r"(?<!\w)socat(?:\s|$)", src)),
            "listen_port": port, "listen_scope": listen_scope,
            "destination_port": dest_port,
            "destination_scope": dest_scope,
            "destination_loopback_socket_observed": "loopback" in listeners or "wildcard" in listeners,
            "destination_matching_socket_count": len(listeners),
            "service_healthy_verified": False}


def assess(graph: dict[str, Any], containers: list[dict[str, Any]],
           unit: str, active: str, sockets: str, mounts: list[dict[str, Any]]) -> dict[str, Any]:
    clients = _clients(containers)
    forward = _forward(unit, active, sockets)
    expected_mount_targets = {"/data", "/import", "/logs"}
    observed = {m.get("Destination") for m in mounts if isinstance(m, dict)}
    mounts_ok = (len(mounts) == 3 and observed == expected_mount_targets
                 and all(m.get("Type") == "bind" and m.get("RW") is True
                         for m in mounts if isinstance(m, dict)))
    risk = []
    if graph.get("production_decision") != "HOLD" or graph.get("change_authorized") is not False:
        raise PreflightHold("upstream_ingress_gate_untrusted")
    if clients["expected_assistx_gateway_clients"] != 3:
        risk.append("required_assistx_client_route_not_verified")
    if not clients["phonelog_host_network_loopback_configured"]:
        risk.append("phonelog_endpoint_network_mode_mismatch")
    if clients["unknown_graph_client_count"] or clients["missing_expected_clients"]:
        risk.append("incomplete_graph_client_inventory")
    if not forward["service_active"] or not forward["binary_is_socat"]:
        risk.append("systemd_forwarder_state_unverified")
    if forward["listen_scope"] != "loopback":
        risk.append("secondary_nonloopback_forwarder")
    if forward["destination_scope"] != "loopback" or not forward["destination_loopback_socket_observed"]:
        risk.append("secondary_forwarder_destination_not_confirmed")
    if not mounts_ok:
        risk.append("persistent_graph_mount_layout_unverified")
    risk.extend(("backup_restore_evidence_unverified",
                 "firewall_and_upstream_reachability_unverified",
                 "database_admin_and_write_role_separation_unverified"))
    return {
        "production_decision": "HOLD", "dispatch_authorized": False,
        "cutover_authorized": False, "read_only": True,
        "ingress": {
            "off_loopback_publication_count": graph.get("off_loopback_publication_count"),
            "network_attachment_count": graph.get("network_attachment_count"),
            "external_reachability_verified": False,
        },
        "clients": clients, "secondary_forwarder": forward,
        "expected_persistent_graph_bind_mounts": mounts_ok,
        "backup_restore_evidence_verified": False,
        "risk_flags": sorted(set(risk)),
        "no_mutations": True,
    }


def collect(expected_image: str) -> dict[str, Any]:
    # The ingress checker performs its own twice-pinned immutable image check.
    try:
        graph = INGRESS["collect"](expected_image)
    except Exception as exc:
        raise PreflightHold("ingress_provenance_unverified") from exc
    ids = _command(["docker", "ps", "-q"], limit=8192).split()
    if not ids or len(ids) > 100 or any(not re.fullmatch(r"[a-f0-9]{12,64}", cid) for cid in ids):
        raise PreflightHold("running_container_ids_unverified")
    raw = _command(["docker", "inspect", *ids])
    try:
        containers = json.loads(raw)
        neo = json.loads(_command(["docker", "inspect", "neo4j"], limit=256000))
    except (ValueError, TypeError) as exc:
        raise PreflightHold("docker_inventory_json_unverified") from exc
    if not isinstance(neo, list) or len(neo) != 1:
        raise PreflightHold("neo4j_runtime_unverified")
    INGRESS["_identity"](neo[0], expected_image)
    unit = _command(["systemctl", "cat", FORWARDER], limit=16000)
    active = _command(["systemctl", "is-active", FORWARDER], limit=100)
    sockets = _command(["ss", "-H", "-lnt"], limit=100000)
    result = assess(graph, containers, unit, active, sockets, neo[0].get("Mounts") or [])
    # Read the image-pinned ingress a second time to fail on drift during
    # the surrounding client/unit/socket inventory.
    try:
        again = INGRESS["collect"](expected_image)
    except Exception as exc:
        raise PreflightHold("ingress_changed_during_dependency_census") from exc
    if again != graph:
        raise PreflightHold("ingress_changed_during_dependency_census")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--json", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = collect(args.expected_image)
    except Exception:
        # Never print untrusted error text, environment, hostname or secrets.
        print(json.dumps({"production_decision":"HOLD",
                          "cutover_authorized":False,
                          "error_type":"read_only_topology_incomplete"},sort_keys=True))
        return 3
    print(json.dumps(receipt, sort_keys=True))
    return 3


if __name__ == "__main__":
    sys.exit(main())
