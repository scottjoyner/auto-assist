"""Minimal bounded authenticated router admin reader, no redirects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit


def fetch_router_ops_summary(
    *, base_url: str, admin_token: str, request_get: Callable[..., Any], timeout: float = 6.0
) -> dict[str, Any]:
    """Read one fixed router-admin endpoint; never forward tokens on redirects."""
    base = base_url.strip().rstrip("/")
    token = admin_token.strip()
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or not token
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("router admin origin or credential missing/invalid")
    if not (0 < timeout <= 10):
        raise ValueError("invalid router admin timeout")
    response = request_get(
        f"{base}/admin/ops/summary",
        timeout=timeout,
        headers={"X-Admin-Token": token},
        allow_redirects=False,
    )
    if 300 <= response.status_code < 400:
        raise ValueError("router admin redirect denied")
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("router admin response not an object")
    return data
