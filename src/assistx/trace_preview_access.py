"""Deny-only payload-preview authorization, independent from proxy headers.

An injected trace auth principal is NOT proof of Basic authentication. No
trusted-header, cookie, or proxy-origin identity may authorize a payload preview
without separately verified Basic credentials and an explicit operator allowlist.
This is an interim research safeguard, not proof of trusted ingress or RBAC.
"""
import hmac
from typing import Optional

from fastapi.security import HTTPBasicCredentials


def basic_preview_permitted(
    principal: str,
    credentials: Optional[HTTPBasicCredentials],
    *,
    configured_user: Optional[str],
    configured_password: Optional[str],
    allowed_users: str,
) -> bool:
    """Fail closed on unconfigured policy, spoofed principal or bad Basic."""
    if (
        not isinstance(principal, str)
        or not credentials
        or not configured_user
        or not configured_password
        or not isinstance(allowed_users, str)
        or len(allowed_users) > 1024
    ):
        return False
    allowed = {item.strip() for item in allowed_users.split(",") if item.strip()}
    if configured_user not in allowed or principal not in allowed:
        return False
    try:
        return (
            hmac.compare_digest(principal.encode(), configured_user.encode())
            and hmac.compare_digest(credentials.username.encode(), configured_user.encode())
            and hmac.compare_digest(credentials.password.encode(), configured_password.encode())
        )
    except (TypeError, ValueError, UnicodeError, AttributeError):
        return False
