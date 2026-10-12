"""Status-only, credential-free check for the legacy router admin-context route.

A secure router replies 401 to anonymous /admin/context; 200 is a security
regression, not a success. Never transmit the shared admin credential, parse
a private response body, follow redirects, or consult proxy environment.
This check is diagnostic only, never release or credential authorization.
"""
from __future__ import annotations

ROUTER_CONTEXT_URL = "http://127.0.0.1:8088/admin/context"

def classify_router_context_status(code: object) -> dict[str, object]:
    if type(code) is not int or not (100 <= code <= 599):
        code = None
    state = (
        "secured" if code == 401 else
        "anonymous_admin_exposure" if code == 200 else
        "unverified"
    )
    return {
        "status": state,
        "http_status": code,
        "auth_required": code == 401,
        "has_context": None,
        "credentials_sent": False,
        "response_body_read": False,
        "release_authorized": False,
    }

def probe_router_context(session_factory=None) -> dict[str, object]:
    # Dependency is imported only when the probe runs; synthetic tests use a
    # fake session and never access the network or real credentials.
    if session_factory is None:
        from requests import Session
        session_factory = Session
    session = None
    response = None
    code = None
    try:
        session = session_factory()
        session.trust_env = False
        response = session.get(
            ROUTER_CONTEXT_URL, timeout=5, allow_redirects=False, stream=True
        )
        code = response.status_code
    except Exception:
        code = None
    finally:
        # A failed cleanup must not be mistaken for a verified 401 receipt.
        try:
            if response is not None:
                response.close()
        except Exception:
            code = None
        try:
            if session is not None:
                session.close()
        except Exception:
            code = None
    return classify_router_context_status(code)
