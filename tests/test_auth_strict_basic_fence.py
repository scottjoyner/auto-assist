"""Offline negative acceptance for optional Basic-only proxy identity fence.

All credentials and header names in this file are synthetic. No fleet access.
"""
from __future__ import annotations

import pytest
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient
from starlette.requests import Request

import assistx.api as api


def _request(identity: str | None = "forged-identity") -> Request:
    headers = (
        [(b"x-synthetic-proxy-identity", identity.encode("ascii"))]
        if identity is not None
        else []
    )
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/fleet/dashboard",
            "headers": headers,
            "query_string": b"",
            "scheme": "http",
            "server": ("localhost", 8000),
            "client": ("127.0.0.1", 8001),
        }
    )


@pytest.fixture()
def synthetic_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "TRUSTED_AUTH_HEADER", "X-Synthetic-Proxy-Identity")
    monkeypatch.setattr(api, "USER", "synthetic-operator")
    monkeypatch.setattr(api, "PASS", "synthetic-password")
    monkeypatch.setattr(api, "ASSISTX_REQUIRE_BASIC_AUTH", True)


def test_strict_basic_rejects_forged_proxy_header_without_basic(
    synthetic_auth: None,
) -> None:
    assert api._auth_user_from_credentials(_request(), None) is None
    assert api._auth_user_from_credentials(
        _request(),
        HTTPBasicCredentials(username="synthetic-operator", password="wrong"),
    ) is None


def test_strict_basic_retains_valid_basic_despite_forged_header(
    synthetic_auth: None,
) -> None:
    assert api._auth_user_from_credentials(
        _request(),
        HTTPBasicCredentials(
            username="synthetic-operator",
            password="synthetic-password",
        ),
    ) == "synthetic-operator"


def test_strict_basic_fails_closed_if_basic_not_configured(
    synthetic_auth: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "PASS", None)
    assert api._auth_user_from_credentials(_request(), None) is None
    assert api._auth_user_from_credentials(
        _request(),
        HTTPBasicCredentials(
            username="synthetic-operator", password="synthetic-password"
        ),
    ) is None


def test_existing_header_mode_unchanged_until_opt_in(
    synthetic_auth: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "ASSISTX_REQUIRE_BASIC_AUTH", False)
    assert api._auth_user_from_credentials(_request(), None) == "forged-identity"


@pytest.mark.parametrize(
    "endpoint",
    ["/fleet-dashboard", "/traces", "/api/fleet/dashboard"],
)
def test_strict_header_denial_precedes_graph_backend(
    synthetic_auth: None, monkeypatch: pytest.MonkeyPatch, endpoint: str,
) -> None:
    def forbidden_neo():
        pytest.fail("unauthenticated request reached Neo4j")

    monkeypatch.setattr(api, "_neo", forbidden_neo)
    # Avoid TestClient lifespan startup: no workers, providers, or side effects.
    client = TestClient(api.app, raise_server_exceptions=True)
    try:
        resp = client.get(
            endpoint,
            headers={"X-Synthetic-Proxy-Identity": "forged-identity"},
        )
    finally:
        client.close()
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Basic"
