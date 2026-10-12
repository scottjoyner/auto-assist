"""Read-only, value-redacted AssistX ingress inventory; never an auth proof.

Docker inspect env *values* are examined only in memory to derive fixed boolean
statuses. They must never be returned, logged, archived, or published.
"""
from __future__ import annotations

from typing import Any
import re

_DOCKER_ID = re.compile(r"[0-9a-f]{64}")


def network_inspection_ids(container: dict[str, Any]) -> list[str]:
    """Resolve only locally attached, exact Docker network IDs; never names."""
    net = container.get("NetworkSettings")
    attached = net.get("Networks") if isinstance(net, dict) else None
    if not isinstance(attached, dict) or len(attached) > 12:
        raise ValueError("untrusted Docker network membership")
    ids: list[str] = []
    for connection in attached.values():
        ident = connection.get("NetworkID") if isinstance(connection, dict) else None
        if not isinstance(ident, str) or not _DOCKER_ID.fullmatch(ident):
            raise ValueError("untrusted Docker network ID")
        if ident in ids:
            raise ValueError("duplicate Docker network ID")
        ids.append(ident)
    return ids


def assess_shared_bridge_peers(
    container: dict[str, Any], network_inspections: list[dict[str, Any]],
) -> dict[str, int | bool]:
    """Reconcile Docker memberships without echoing peer IDs or addresses.

    Attached Docker peers indicate a reachable-network concern, NOT a
    demonstrated forged-header exploit and NOT an admission verdict.
    """
    container_id = container.get("Id")
    if not isinstance(container_id, str) or not _DOCKER_ID.fullmatch(container_id):
        raise ValueError("missing exact Docker container identity")
    expected = set(network_inspection_ids(container))
    if (not isinstance(network_inspections, list)
            or len(network_inspections) != len(expected)):
        raise ValueError("incomplete Docker network inventory")
    seen: set[str] = set()
    unique_peers: set[str] = set()
    attachments = 0
    bridge_networks = 0
    for network in network_inspections:
        if not isinstance(network, dict):
            raise ValueError("invalid Docker network metadata")
        ident = network.get("Id")
        members = network.get("Containers")
        if (not isinstance(ident, str) or ident not in expected
                or ident in seen or not isinstance(members, dict)
                or len(members) > 512 or container_id not in members):
            raise ValueError("unbound Docker network inventory")
        seen.add(ident)
        if network.get("Driver") != "bridge":
            continue
        bridge_networks += 1
        for peer_id in members:
            if not isinstance(peer_id, str) or not _DOCKER_ID.fullmatch(peer_id):
                raise ValueError("invalid Docker peer identity")
            if peer_id != container_id:
                attachments += 1
                unique_peers.add(peer_id)
    if seen != expected:
        raise ValueError("missing Docker network")
    return {
        "bridge_network_count": bridge_networks,
        "bridge_peer_attachments": attachments,
        "unique_bridge_peer_count": len(unique_peers),
        "shared_bridge_peers_present": bool(unique_peers),
    }


_AUTH_KEYS = {
    "TRUSTED_AUTH_HEADER",
    "ASSISTX_REQUIRE_BASIC_AUTH",
    "BASIC_AUTH_USER",
    "BASIC_AUTH_PASS",
    "KIPNERTER_TAILNET_ALLOWED_LOGINS",
}
_TRUE = {"1", "true", "yes", "on"}
_LOCAL = {"127.0.0.1", "::1"}


def assess_ingress_boundary(
    container: dict[str, Any], serve_report: dict[str, Any],
    *, network_inspections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return booleans/reason-codes only; even a good layout is not SSO proof."""
    if (not isinstance(container, dict) or container.get("Name") != "/assistx-api"
            or not isinstance(serve_report, dict)
            or serve_report.get("schema") != "fleet-serve-route-preflight/v1"):
        raise ValueError("invalid ingress metadata")
    config, net = container.get("Config"), container.get("NetworkSettings")
    if not isinstance(config, dict) or not isinstance(net, dict):
        raise ValueError("invalid ingress metadata")
    source = config.get("Env")
    if not isinstance(source, list) or len(source) > 512:
        raise ValueError("invalid ingress metadata")
    flags: dict[str, str] = {}
    for entry in source:
        if not isinstance(entry, str) or len(entry) > 8192 or "=" not in entry:
            raise ValueError("invalid ingress metadata")
        key, value = entry.split("=", 1)
        if key in _AUTH_KEYS:
            if key in flags:
                raise ValueError("duplicate ingress control")
            flags[key] = value
    portmap = net.get("Ports")
    if not isinstance(portmap, dict):
        raise ValueError("invalid ingress metadata")
    bindings = portmap.get("8000/tcp")
    if isinstance(bindings, list) and bindings:
        loopback = all(
            isinstance(item, dict)
            and item.get("HostIp") in _LOCAL
            and item.get("HostPort") == "8000"
            for item in bindings
        )
    else:
        loopback = False
    trusted = bool(flags.get("TRUSTED_AUTH_HEADER", "").strip())
    strict = flags.get("ASSISTX_REQUIRE_BASIC_AUTH", "").strip().lower() in _TRUE
    allowlist = bool(flags.get("KIPNERTER_TAILNET_ALLOWED_LOGINS", "").strip())
    present = serve_report.get("explicit_routes_present_somewhere")
    observation_mapped = (
        isinstance(present, dict) and present.get("observations") is True
    )
    topology = (
        assess_shared_bridge_peers(container, network_inspections)
        if network_inspections is not None else None
    )
    reasons = [
        "identity_header_strip_and_injection_unverified",
        "same_bridge_peer_negative_unverified",
        "independent_gateway_attestation_unverified",
        "physical_client_authorization_unverified",
    ]
    if topology is None:
        reasons.append("docker_peer_topology_not_examined")
    elif topology["shared_bridge_peers_present"]:
        reasons.append("shared_docker_bridge_peers_attached")
    if trusted and not strict:
        reasons.append("header_only_identity_precedes_basic")
    if not loopback:
        reasons.append("api_host_binding_not_proven_loopback_only")
    if not allowlist:
        reasons.append("mobile_login_allowlist_unconfigured")
    if not observation_mapped:
        reasons.append("observation_route_not_explicitly_mapped")
    return {
        "schema": "fleet-ingress-auth-inventory/v1",
        "evidence": "docker_config_and_serve_metadata_only",
        "redacted": True,
        "trusted_identity_header_configured": trusted,
        "strict_basic_auth_configured": strict,
        "basic_credentials_configured": bool(flags.get("BASIC_AUTH_USER"))
        and bool(flags.get("BASIC_AUTH_PASS")),
        "mobile_login_allowlist_configured": allowlist,
        "api_8000_host_binding_loopback_only": bool(loopback),
        "docker_peer_bypass_ruled_out": False,
        "docker_network_inventory_complete": topology is not None,
        "docker_bridge_network_count": (
            topology["bridge_network_count"] if topology is not None else None
        ),
        "docker_bridge_peer_attachments": (
            topology["bridge_peer_attachments"] if topology is not None else None
        ),
        "docker_unique_bridge_peer_count": (
            topology["unique_bridge_peer_count"] if topology is not None else None
        ),
        "docker_shared_bridge_peers_present": (
            topology["shared_bridge_peers_present"] if topology is not None else None
        ),
        "identity_header_provenance_verified": False,
        "signed_gateway_receiver_wired_and_verified": False,
        "physical_negative_ingress_verified": False,
        "observation_route_explicitly_mapped": observation_mapped,
        "reasons": sorted(reasons),
        "model_requests": 0,
        "model_authorizations": 0,
        "acceptance": "HOLD",
    }
