"""Read-only Tailscale Serve route assessment for AssistX fleet observations.

Configuration alone never proves that HTTPS listeners or backend services work.
No network calls, service changes, credential access, or model dispatch here.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

REQUIRED = {
    "identity": "/api/v1/auth/whoami",
    "catalog": "/api/v1/runtime/catalog",
    "observations": "/api/v1/runtime/observations",
}


def _listener_from_serve_key(key: Any) -> tuple[str, int] | None:
    if not isinstance(key, str) or not key:
        return None
    try:
        parsed = urlsplit("https://" + key)
        if (not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port is None
                or parsed.path or parsed.query or parsed.fragment):
            return None
        return parsed.hostname.rstrip(".").lower(), parsed.port
    except ValueError:
        return None


def _self_dns(status: dict[str, Any]) -> str | None:
    owner = status.get("Self")
    if not isinstance(owner, dict):
        return None
    candidate = owner.get("DNSName")
    if not isinstance(candidate, str):
        return None
    host = candidate.rstrip(".").lower()
    if not host.endswith(".ts.net") or host == ".ts.net":
        return None
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,250}\.ts\.net", host):
        return None
    return host

def assess_serve_configuration(
    serve: dict[str, Any],
    status: dict[str, Any],
) -> dict[str, Any]:
    """Assess only *explicit* routes for this machine's own Serve identity.

    A root catch-all may proxy unlisted paths; missing explicit handlers is
    therefore a verification gap, not proof of a 404 or disabled backend.
    """
    if not isinstance(serve, dict) or not isinstance(status, dict):
        raise ValueError("invalid Tailscale status data")
    host = _self_dns(status)
    if not host:
        raise ValueError("Tailscale local identity unavailable")
    web = serve.get("Web")
    if not isinstance(web, dict) or len(web) > 100:
        raise ValueError("invalid Tailscale Serve Web map")
    matched: list[tuple[int, dict]] = []
    for key, info in web.items():
        listener = _listener_from_serve_key(key)
        if listener is not None and listener[0] == host:
            if not isinstance(info, dict) or not isinstance(info.get("Handlers"), dict):
                raise ValueError("invalid Serve handler document")
            matched.append((listener[1], info["Handlers"]))
    # Never combine distinct listeners as one imaginary same-host service.
    identity_and_observation = []
    for port, handlers in matched:
        registered = {
            name: (
                isinstance(handlers.get(route), dict)
                and bool(handlers[route].get("Proxy"))
            )
            for name, route in REQUIRED.items()
        }
        identity_and_observation.append({
            "port": port,
            "explicit_routes": registered,
            "root_catchall_present": (
                isinstance(handlers.get("/"), dict)
                and bool(handlers["/"].get("Proxy"))
            ),
        })
    complete = any(all(x["explicit_routes"].values()) for x in identity_and_observation)
    has_root = any(x["root_catchall_present"] for x in identity_and_observation)
    missing = sorted({
        route
        for key, route in REQUIRED.items()
        if not any(v["explicit_routes"][key] for v in identity_and_observation)
    })
    return {
        "schema": "fleet-serve-route-preflight/v1",
        "mode": "configuration_only",
        "local_tailnet_host_digest": hashlib.sha256(host.encode()).hexdigest(),
        "matching_https_listener_configs": len(matched),
        "configured_https_ports": sorted({port for port, _ in matched}),
        "identity_route_https_ports": sorted({
            item["port"] for item in identity_and_observation
            if item["explicit_routes"]["identity"]
        }),
        "explicit_routes_complete_on_one_listener": complete,
        "explicit_routes_present_somewhere": {
            name: any(row["explicit_routes"][name] for row in identity_and_observation)
            for name in REQUIRED
        },
        "missing_explicit_routes": missing,
        "root_catchall_present": has_root,
        "route_gap_assessment": (
            "explicit_routes_complete_unverified_live"
            if complete else
            "implicit_proxy_possible_live_check_required"
            if has_root else
            "explicit_route_mapping_incomplete"
        ),
        "acceptance": "HOLD",
        "authenticated_transport_verified": False,
        "backend_http_verified": False,
        "source_witness_bound": False,
        "production_admitted": False,
        "model_dispatch_allowed": False,
        "router_mutations": 0,
        "provider_requests": 0,
    }
