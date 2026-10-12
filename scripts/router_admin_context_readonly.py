"""Fail-closed read-only Auto-Router admin context status, without body access.

The two legacy script consumers must not treat anonymous HTTP 200 as healthy
when the router is cut over to authenticated admin endpoints.
"""
from __future__ import annotations

import os
from urllib import error, request

# Not configurable by environment: never forward credentials to arbitrary hosts.
_URL = "http://127.0.0.1:8088/admin/context"
_SECRET_ENV = "AUTO_ROUTER_ADMIN_TOKEN"


class _RejectRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_admin_context(timeout: float = 5.0) -> dict:
    """Return status metadata only; no response body, header, token or URL."""
    token = os.environ.get(_SECRET_ENV, "")
    if not token.strip():
        return {"status": "blocked", "http_status": None,
                "reason": "admin_token_missing", "has_context": None}
    req = request.Request(
        _URL, headers={"X-Admin-Token": token}, method="GET"
    )
    opener = request.build_opener(request.ProxyHandler({}), _RejectRedirect())
    try:
        with opener.open(req, timeout=timeout) as response:
            status = response.status
    except error.HTTPError as exc:
        status = exc.code
    except (error.URLError, OSError, TimeoutError):
        return {"status": "unreachable", "http_status": None,
                "reason": "transport_unverified", "has_context": None}
    # A status-only health receipt is never proof of actual context contents.
    return {"status": "ok" if status == 200 else "error",
            "http_status": status, "reason": "authenticated_admin_response",
            "has_context": None}
