"""Short-lived WebSocket tokens.

A browser cannot send an Authorization header on a WebSocket handshake, and
the static WS_AUTH_TOKEN is a server-side secret. The API therefore mints a
short-lived, domain-separated token for an authenticated session. These tests
pin the security properties: expiry, tamper detection, domain separation, and
that the static token keeps working for server-side clients.
"""

from __future__ import annotations

import time

import pytest

from assistx import api


@pytest.fixture(autouse=True)
def _ws_secret(monkeypatch):
    monkeypatch.setattr(api, "WS_AUTH_TOKEN", "server-side-secret", raising=False)
    monkeypatch.setattr(api, "WS_AUTH_REQUIRED", True, raising=False)
    monkeypatch.setattr(api, "WS_TOKEN_TTL_SECONDS", 300, raising=False)


def test_minted_token_is_accepted_by_the_ws_guard():
    minted = api._mint_ws_token()
    api._require_ws_auth(minted["token"])  # must not raise


def test_minted_token_is_refused_by_http_api_auth():
    """Domain separation: a WS token must not authenticate HTTP calls.

    The HTTP guard only accepts basic-auth credentials (or the trusted auth
    header), so a minted WebSocket token presented as a password gets
    nowhere even though the same secret signed it.
    """
    from types import SimpleNamespace

    minted = api._mint_ws_token()
    request = SimpleNamespace(headers={})
    credentials = SimpleNamespace(username="operator", password=minted["token"])
    monkeypatched_user = getattr(api, "USER", None)
    monkeypatched_pass = getattr(api, "PASS", None)
    api.USER, api.PASS = "operator", "some-real-password"
    try:
        assert api._auth_user_from_credentials(request, credentials) is None
    finally:
        api.USER, api.PASS = monkeypatched_user, monkeypatched_pass


def test_expired_token_is_refused():
    from fastapi import HTTPException

    minted = api._mint_ws_token(now=time.time() - 10_000)
    with pytest.raises(HTTPException) as exc:
        api._require_ws_auth(minted["token"])
    assert exc.value.status_code == 401


def test_tampered_signature_is_refused():
    from fastapi import HTTPException

    token = api._mint_ws_token()["token"]
    expires, _, signature = token.partition(".")
    for bad in (f"{expires}.{'0' * len(signature)}", f"{expires}x.{signature}", f".{signature}", "garbage", ""):
        with pytest.raises(HTTPException) as exc:
            api._require_ws_auth(bad)
        assert exc.value.status_code == 401


def test_signature_covers_the_declared_purpose():
    """The purpose must be *inside* the signature, not just documented.

    Re-signing with the module's declared purpose must validate, so removing the
    purpose from the signed payload breaks this test.
    """
    import hashlib
    import hmac

    expires = int(time.time()) + 300
    signature = hmac.new(
        api.WS_AUTH_TOKEN.encode("utf-8"),
        f"{expires}|{api._WS_TOKEN_PURPOSE}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert api._ws_token_is_valid(f"{expires}.{signature}") is True


def test_token_is_not_accepted_for_a_different_purpose():
    """Re-signing the same expiry under another domain must not validate."""
    import hashlib
    import hmac

    expires = int(time.time()) + 300
    forged = hmac.new(
        api.WS_AUTH_TOKEN.encode("utf-8"),
        f"{expires}|some.other.purpose".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert api._ws_token_is_valid(f"{expires}.{forged}") is False


def test_static_token_still_works_for_server_clients():
    api._require_ws_auth("server-side-secret")  # must not raise


def test_ws_guard_is_open_when_ws_auth_is_disabled(monkeypatch):
    monkeypatch.setattr(api, "WS_AUTH_REQUIRED", False, raising=False)
    api._require_ws_auth(None)  # must not raise


def test_missing_secret_is_a_clear_503(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(api, "WS_AUTH_TOKEN", "", raising=False)
    with pytest.raises(HTTPException) as exc:
        api._require_ws_auth("anything")
    assert exc.value.status_code == 503
    with pytest.raises(HTTPException) as mint_exc:
        api._mint_ws_token()
    assert mint_exc.value.status_code == 503
