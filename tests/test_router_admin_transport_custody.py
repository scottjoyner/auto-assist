"""Pure synthetic checks for admin-transport evidence without leaking URL or secrets."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "router_admin_transport_custody.py"
spec = importlib.util.spec_from_file_location("router_admin_transport_custody", SOURCE)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

FAKE_TOKEN = "synthetic-shared-admin-token"


def test_current_http_pattern_requires_explicit_review():
    result = app.classify_router_admin_transport("http://synthetic-router.internal:8088", FAKE_TOKEN)
    assert result["reason"] == "PLAINTEXT_SHARED_ADMIN_CREDENTIAL_NEEDS_OWNER_EXCEPTION"
    assert result["configured_transport_scheme"] == "HTTP"
    assert result["decision"] == "HOLD_TRANSPORT_REVIEW"
    assert result["production_release_authorized"] is False


def test_https_still_requires_peer_verification():
    result = app.classify_router_admin_transport("https://synthetic-router.internal:8443", FAKE_TOKEN)
    assert result["reason"] == "TLS_PEER_IDENTITY_NOT_INDEPENDENTLY_VERIFIED"
    assert result["tls_peer_identity_verified"] is False


@pytest.mark.parametrize("url", [
    "", None, "not-an-origin", "ftp://synthetic-router:8088",
    "http://u:p@synthetic-router:8088",
    "http://synthetic-router:8088/?secret=synthetic",
    "http://synthetic-router:8088/#fragment",
    "http://synthetic-router:99999",
    "http://synthetic-router:8088/admin/runtime-projection",
])
def test_invalid_origin_never_proves_readiness(url):
    result = app.classify_router_admin_transport(url, FAKE_TOKEN)
    assert result["decision"] == "HOLD_TRANSPORT_REVIEW"
    assert result["configured_transport_scheme"] == "INVALID"


@pytest.mark.parametrize("token", ["", None, "   "])
def test_missing_admin_token_is_denial(token):
    result = app.classify_router_admin_transport("http://synthetic-router:8088", token)
    assert result["reason"] == "ADMIN_TOKEN_MISSING_OR_EMPTY"
    assert result["admin_token_configured"] is False


def test_never_echoes_url_secret_or_hostname():
    fake_url = "http://privileged-user:hidden@unique-router-host.invalid:8088/?token=abc"
    result = app.classify_router_admin_transport(fake_url, FAKE_TOKEN)
    output = str(result)
    assert "unique-router-host.invalid" not in output
    assert "hidden" not in output
    assert "privileged-user" not in output
    assert "synthetic-shared-admin-token" not in output
    assert "token=abc" not in output
    assert result["credential_value_or_hash_reported"] is False


def test_no_dns_or_network_calls_implied():
    result = app.classify_router_admin_transport("https://router.example.invalid", FAKE_TOKEN)
    assert result["dns_resolved"] is False
    assert result["http_request_sent"] is False
    assert result["target_address_reported"] is False
    assert result["owner_security_exception_verified"] is False
    assert result["credential_custody_closed"] is False
