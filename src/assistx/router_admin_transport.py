"""Fail-closed transport for AssistX's privileged Router ops-summary read.

Never use this for public health/model discovery, inference, or state changes.
The configured Router base URL is operator-controlled. Never forward the admin
token through environment proxies or HTTP redirects. No credential logging.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import requests


class RouterAdminReadDenied(RuntimeError):
    """A deliberate non-secret failure code."""


def fetch_router_admin_ops_summary(
    base_url: str, admin_token: str, *, timeout: float = 6.0
) -> dict[str, Any]:
    token = admin_token.strip() if isinstance(admin_token, str) else ""
    if not token:
        raise RouterAdminReadDenied("router_admin_token_missing")
    if not isinstance(base_url, str):
        raise RouterAdminReadDenied("router_admin_base_url_invalid")
    base = base_url.strip().rstrip("/")
    try:
        parsed = urlsplit(base)
    except ValueError as exc:
        raise RouterAdminReadDenied("router_admin_base_url_invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RouterAdminReadDenied("router_admin_base_url_invalid")
    target = base + "/admin/ops/summary"
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(
                target,
                headers={"Accept": "application/json", "X-Admin-Token": token},
                allow_redirects=False,
                timeout=timeout,
            )
            if 300 <= response.status_code < 400:
                raise RouterAdminReadDenied("router_admin_redirect_denied")
            if response.status_code in {401, 403}:
                raise RouterAdminReadDenied("router_admin_auth_denied")
            response.raise_for_status()
            payload = response.json()
    except RouterAdminReadDenied:
        raise
    except (requests.RequestException, ValueError, TypeError) as exc:
        # Do not echo potentially credential-bearing exception strings or URLs.
        raise RouterAdminReadDenied("router_admin_read_unavailable") from exc
    if not isinstance(payload, dict):
        raise RouterAdminReadDenied("router_admin_response_invalid")
    return payload
