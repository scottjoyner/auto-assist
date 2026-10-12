"""Explicitly opt-in HTTPS self-probe for three read-only AssistX API routes.

No discovery or model execution. TLS is checked against system trust; redirects
and inherited HTTP proxies are prohibited. All response bodies stay in memory.
"""
from __future__ import annotations

import json
import re
import ssl
from typing import Any, Callable
from urllib import request, error

from .fleet_sso_ingest import sso_identity_consistent

ROUTES = {
    "identity": "/api/v1/auth/whoami",
    "catalog": "/api/v1/runtime/catalog",
    "observations": "/api/v1/runtime/observations",
}
MAX_RESPONSE = 64_000


class _RejectRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _trusted_read(url: str) -> tuple[int, bytes | None]:
    client = request.build_opener(
        request.ProxyHandler({}),
        request.HTTPSHandler(context=ssl.create_default_context()),
        _RejectRedirects(),
    )
    req = request.Request(
        url, method="GET",
        headers={"Accept": "application/json", "Cache-Control": "no-store"},
    )
    try:
        with client.open(req, timeout=4.0) as res:
            if res.geturl() != url:
                raise ValueError("unrequested redirect")
            blob = res.read(MAX_RESPONSE + 1)
            if len(blob) > MAX_RESPONSE:
                raise ValueError("large reply")
            return int(res.status), blob
    except error.HTTPError as exc:
        # TLS succeeded even for 401/403/404; do not reveal error body.
        return int(exc.code), None

def probe_self(
    hostname: str,
    port: int,
    *,
    transport: Callable[[str], tuple[int, bytes | None]] | None = None,
) -> dict[str, Any]:
    """Called only after own Tailscale hostname and Serve listener validation.

    Injected transports are offline test doubles, never accepted as live TLS.
    """
    if (not isinstance(hostname, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,250}\.ts\.net", hostname)
            or type(port) is not int or not 1 <= port <= 65535):
        raise ValueError("unsupported local Tailscale endpoint")
    reader = transport if transport is not None else _trusted_read
    results: dict[str, dict[str, Any]] = {}
    for key, suffix in ROUTES.items():
        target = "https://" + hostname + ":" + str(port) + suffix
        row: dict[str, Any] = {"http_status": None, "transport_state": "error"}
        try:
            status, body = reader(target)
            if type(status) is not int or not 100 <= status <= 599:
                raise ValueError("invalid HTTP code")
            row["http_status"] = status
            row["transport_state"] = "http_response"
            if key == "identity" and status == 200:
                row["tailscale_whoami_contract_valid"] = False
                parsed = json.loads(body) if isinstance(body, bytes) else None
                row["tailscale_whoami_contract_valid"] = bool(
                    isinstance(parsed, dict) and sso_identity_consistent(parsed)
                )
            if key == "catalog" and status == 200:
                row["catalog_schema_v2"] = False
                parsed = json.loads(body) if isinstance(body, bytes) else None
                row["catalog_schema_v2"] = bool(
                    isinstance(parsed, dict)
                    and parsed.get("schema_version") == "2"
                    and isinstance(parsed.get("models"), list)
                    and isinstance(parsed.get("runtimes"), list)
                )
            if key == "observations" and status == 200:
                row["observational_only_schema"] = False
                parsed = json.loads(body) if isinstance(body, bytes) else None
                row["observational_only_schema"] = bool(
                    isinstance(parsed, dict)
                    and parsed.get("schema_version") == 1
                    and parsed.get("source") == "tailnet_observational_only"
                    and parsed.get("authority") == "not_admitted"
                )
        except (OSError, ValueError, TypeError, UnicodeError, error.URLError):
            if row["transport_state"] == "http_response":
                row["response_contract_invalid"] = True
        results[key] = row
    return {
        "schema": "fleet-serve-https-self-probe/v1",
        "mode": "three_read_only_requests",
        "https_listener_port": port,
        "system_tls_validation_attempted": transport is None,
        "transport_test_double": transport is not None,
        "routes": results,
        "gateway_sso_claim_verifiable": (
            transport is None
            and results["identity"].get("tailscale_whoami_contract_valid") is True
        ),
        "trusted_reverse_proxy_header_boundary_verified": False,
        "private_observation_source_bound": False,
        "model_authorizations": 0,
        "model_requests": 0,
        "acceptance": "HOLD",
    }
