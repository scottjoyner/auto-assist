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

_OBSERVATION_STATES = {"fresh", "stale", "invalid", "unavailable", "not_configured"}
_MODEL_STATES = {"resident_verified", "advertised_unverified", "installed_not_loaded"}
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ +:\\-]{0,99}")
_IPV4_LIKE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?")
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")


def _observation_contract(parsed: Any) -> tuple[bool, bool, str]:
    """Validate the sanitized, strictly non-actionable mobile observation shape.

    A valid stale/empty response is *not* fresh observation evidence.
    This checks data presentation only, never source custody or SSO transport.
    """
    if (not isinstance(parsed, dict)
            or type(parsed.get("schema_version")) is not int
            or parsed["schema_version"] != 1
            or parsed.get("source") != "tailnet_observational_only"
            or parsed.get("authority") != "not_admitted"
            or parsed.get("state") not in _OBSERVATION_STATES):
        return False, False, "invalid_contract"
    state = parsed["state"]
    models = parsed.get("models")
    if (not isinstance(models, list) or len(models) > 80
            or type(parsed.get("truncated")) is not bool
            or any(type(parsed.get(key)) is not int or parsed[key] < 0
                   for key in ("observed_nodes", "observed_endpoints", "resident_endpoints"))):
        return False, False, "invalid_contract"
    for item in models:
        if not isinstance(item, dict) or set(item) != {
            "display_name", "state", "observed_instances", "admitted", "selectable"
        }:
            return False, False, "invalid_contract"
        name = item.get("display_name")
        if (not isinstance(name, str) or not _SAFE_NAME.fullmatch(name)
                or ".." in name or _IPV4_LIKE.fullmatch(name)
                or item.get("state") not in _MODEL_STATES
                or type(item.get("observed_instances")) is not int
                or item["observed_instances"] < 1
                or item.get("admitted") is not False
                or item.get("selectable") is not False):
            return False, False, "invalid_contract"
    if state != "fresh":
        if models or parsed["truncated"] or any(
            parsed[key] != 0 for key in ("observed_nodes", "observed_endpoints", "resident_endpoints")
        ):
            return False, False, "invalid_contract"
    else:
        if (not isinstance(parsed.get("observation_sha256"), str)
                or not _HEX_DIGEST.fullmatch(parsed["observation_sha256"])
                or not isinstance(parsed.get("source_inventory_sha256"), str)
                or not _HEX_DIGEST.fullmatch(parsed["source_inventory_sha256"])):
            return False, False, "invalid_contract"
    return True, state == "fresh", state


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
                parsed = json.loads(body) if isinstance(body, bytes) else None
                valid, fresh, safe_state = _observation_contract(parsed)
                row["observational_only_schema"] = valid
                row["fresh_nonselectable_observations"] = valid and fresh
                row["observations_state"] = safe_state
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
