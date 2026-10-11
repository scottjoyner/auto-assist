"""Bounded, authenticated, read-only Auto-Router context status for scripts.

Never probe a privileged route anonymously or forward admin credentials across
redirects. Keep response contents and token values out of diagnostics.
"""

from __future__ import annotations

import os


def probe_router_context(requests_module):
    """Return explicit status without changing router or local runtime state."""
    token = os.environ.get("AUTO_ROUTER_ADMIN_TOKEN")
    if not token:
        return {"status": "auth_required", "has_context": None}
    try:
        response = requests_module.get(
            "http://localhost:8088/admin/context",
            headers={"X-Admin-Token": token},
            timeout=5,
            allow_redirects=False,
        )
    except Exception:
        # Avoid putting remote error strings (potentially containing URLs)
        # into coordination reports and logs.
        return {"status": "unreachable", "has_context": None}
    if response.status_code in (401, 403):
        return {"status": "unauthorized", "has_context": None, "http_status": response.status_code}
    if 300 <= response.status_code < 400:
        return {"status": "redirect_rejected", "has_context": None, "http_status": response.status_code}
    if response.status_code != 200:
        return {"status": "error", "has_context": None, "http_status": response.status_code}
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return {"status": "invalid_response", "has_context": None}
    if not isinstance(payload, (dict, list)):
        return {"status": "invalid_response", "has_context": None}
    return {"status": "ok", "has_context": bool(payload)}
