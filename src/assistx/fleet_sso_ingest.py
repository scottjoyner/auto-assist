"""Read-only Tailnet SSO observation source; never authorizes dispatch."""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any
from urllib import parse

from .fleet_endpoint_shadow_gate import bind_mobile_projection

MAX_BYTES = 2_000_000


def tailnet_origin(raw: str) -> str:
    parsed = parse.urlsplit(raw)
    if (parsed.scheme != "https" or not parsed.hostname
            or not parsed.hostname.lower().endswith(".ts.net")
            or parsed.hostname.lower() == ".ts.net"
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443) or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("HTTPS Tailscale Serve origin required")
    return "https://" + parsed.hostname.lower()

def sso_identity_matches(payload: dict[str, Any]) -> bool:
    return (
        payload.get("authenticated") is True
        and payload.get("provider") == "tailscale"
        and isinstance(payload.get("login"), str)
        and bool(payload["login"].strip())
        and isinstance(payload.get("account_id"), str)
        and re.fullmatch(r"tailscale:[0-9a-f]{24}", payload["account_id"]) is not None
    )

def sso_identity_consistent(payload: dict[str, Any]) -> bool:
    """Match account ID derivation in AssistX's Tailnet mobile API."""
    if not sso_identity_matches(payload):
        return False
    login = payload["login"].strip().lower()
    expected = "tailscale:" + hashlib.sha256(login.encode("utf-8")).hexdigest()[:24]
    return payload["account_id"] == expected


def untrusted_replay(
    origin: str,
    whoami: dict[str, Any],
    observation: dict[str, Any],
    private_source: bytes,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Inspect supplied response documents without asserting transport identity."""
    root = tailnet_origin(origin)
    instant = time.time() if now is None else now
    identity_match = sso_identity_consistent(whoami)
    binding = bind_mobile_projection(observation, private_source, now=instant)
    accepted = identity_match and binding["source_binding_valid"]
    return {
        "schema": "fleet-sso-replay/v1",
        "status": "LOCAL_CONTRACT_PASS" if accepted else "REJECT",
        "identity_contract_pass": identity_match,
        "source_binding_valid": binding["source_binding_valid"],
        "binding_status": binding["status"],
        "binding_reasons": binding["reasons"],
        "observation_model_count": binding.get("sanitized_model_count", 0),
        "endpoint_origin_sha256": hashlib.sha256(root.encode()).hexdigest(),
        "live_https_verified": False,
        "sso_transport_authenticated": False,
        "cross_node_lease_authorized": False,
        "provider_requests": 0,
        "model_dispatches": 0,
        "authority": "observation_only",
    }
