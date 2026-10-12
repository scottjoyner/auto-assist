"""Value-free read-only classification of a configured Auto-Router admin transport.

Never resolves DNS, sends requests, prints URL/token values, or asserts TLS trust.
This CLI is an operator review witness, not production-release authorization.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit


def classify_router_admin_transport(base_url: object, admin_token: object) -> dict[str, object]:
    decision = "HOLD_TRANSPORT_REVIEW"
    reason = "UNKNOWN_OR_INVALID_ADMIN_ORIGIN"
    scheme = "INVALID"
    if not isinstance(base_url, str) or not base_url.strip():
        reason = "NO_CONFIGURED_ADMIN_ORIGIN"
    else:
        try:
            parsed = urlsplit(base_url)
            port = parsed.port
            valid = bool(
                parsed.scheme in ("http", "https")
                and parsed.hostname
                and (port is None or 1 <= port <= 65535)
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
                and parsed.path in ("", "/")
            )
            if valid:
                scheme = parsed.scheme.upper()
                reason = (
                    "PLAINTEXT_SHARED_ADMIN_CREDENTIAL_NEEDS_OWNER_EXCEPTION"
                    if parsed.scheme == "http"
                    else "TLS_PEER_IDENTITY_NOT_INDEPENDENTLY_VERIFIED"
                )
        except (ValueError, TypeError):
            pass
    has_token = isinstance(admin_token, str) and bool(admin_token.strip())
    if not has_token:
        reason = "ADMIN_TOKEN_MISSING_OR_EMPTY"
    return {
        "schema_version": "assistx.admin-transport-custody-v1",
        "decision": decision,
        "reason": reason,
        "configured_transport_scheme": scheme,
        "admin_token_configured": has_token,
        "target_hostname_reported": False,
        "target_address_reported": False,
        "credential_value_or_hash_reported": False,
        "dns_resolved": False,
        "http_request_sent": False,
        "tls_peer_identity_verified": False,
        "owner_security_exception_verified": False,
        "credential_custody_closed": False,
        "production_release_authorized": False,
    }


def main() -> int:
    result = classify_router_admin_transport(
        os.environ.get("AUTO_ROUTER_BASE_URL"),
        os.environ.get("AUTO_ROUTER_ADMIN_TOKEN"),
    )
    print(json.dumps(result, sort_keys=True))
    # Never return a release-green result merely for having a configured URL.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
