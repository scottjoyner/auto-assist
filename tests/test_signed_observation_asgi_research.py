"""Synthetic signed observation ASGI contract; not a deployed API route."""
from __future__ import annotations

import base64
import sys
import types
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

# This is an intentionally standalone security lane, not an AssistX app test.
# Loading src/assistx/__init__.py would import production config/LLM clients
# and unrelated dependencies. Register an import-only package path instead.
_PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "assistx"
if "assistx" not in sys.modules:
    _package = types.ModuleType("assistx")
    _package.__path__ = [str(_PACKAGE_DIR)]
    sys.modules["assistx"] = _package

from assistx.signed_observation_asgi_research import verify_observation_asgi_identity
from assistx.trusted_gateway_identity_contract import (
    GatewayIdentityDenied, VERSION, ISSUER, AUDIENCE, PREFIX, canonical_claim,
)

NOW = 1_800_000_000_000
SUBJECT = "synthetic-observer@example.invalid"
KEY_ID = "synthetic-receiver-key"
PATH = "/api/v1/runtime/observations"


def fixture(**changes):
    private = Ed25519PrivateKey.generate()
    pub = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    claim = {
        "version": VERSION, "issuer": ISSUER, "audience": AUDIENCE,
        "key_id": KEY_ID, "subject": SUBJECT,
        "issued_at_ms": NOW - 1_000, "expires_at_ms": NOW + 12_000,
        "nonce": "1" * 32, "method": "GET", "target": PATH,
        "scope": "model:observe",
    }
    claim.update(changes)
    raw = canonical_claim(claim)
    signature = private.sign(PREFIX + raw)
    return pub, [
        (b"x-assistx-gateway-claim", base64.urlsafe_b64encode(raw).rstrip(b"=")),
        (b"x-assistx-gateway-signature", base64.urlsafe_b64encode(signature).rstrip(b"=")),
    ]


def scope(path=PATH, method="GET", query=b""):
    return {"type": "http", "method": method, "raw_path": path.encode(),
            "path": path, "query_string": query}


def verify(public, headers, *, context=None, once=None, now=NOW):
    return verify_observation_asgi_identity(
        scope=scope() if context is None else context,
        headers=headers, receiver_public_key=public, receiver_key_id=KEY_ID,
        now_ms=now,
        consume_nonce_once=(lambda *_: True) if once is None else once,
    )


def test_valid_signed_observation_exact_method_and_nonce_once():
    public, headers = fixture()
    seen = set()
    def first(subject, nonce, expiry):
        key = subject, nonce
        if key in seen:
            return False
        seen.add(key)
        return True
    assert verify(public, headers, once=first) == SUBJECT
    with pytest.raises(GatewayIdentityDenied):
        verify(public, headers, once=first)


@pytest.mark.parametrize("payload", [
    [], [(b"Tailscale-User-Login", b"forged@example.invalid")],
    [(b"Authorization", b"Basic synthetic")],
])
def test_legacy_header_only_never_authenticates(payload):
    pub, _ = fixture()
    with pytest.raises(GatewayIdentityDenied):
        verify(pub, payload)


@pytest.mark.parametrize("change", [
    {"method": "POST"}, {"target": "/api/v1/runtime/catalog"},
    {"target": "/api/v1/runtime/observations?x=1"},
    {"scope": "model:load"}, {"subject": "not-an-email"},
    {"expires_at_ms": NOW - 1_000},
])
def test_signed_claim_for_wrong_operation_does_not_consume_nonce(change):
    public, headers = fixture(**change)
    with pytest.raises(GatewayIdentityDenied):
        verify(public, headers, once=lambda *_: pytest.fail("consumed wrong nonce"))


@pytest.mark.parametrize("context", [
    scope(path="/api/v1/runtime/catalog"),
    scope(path="/api/v1/runtime/observations/", method="GET"),
    scope(path=PATH,method="POST"),
    scope(query=b"limit=50"),
    {**scope(), "raw_path": None},
    {**scope(), "raw_path": b"/api/v1/runtime/observations%2f"},
])
def test_mismatched_request_route_method_or_raw_target_denied(context):
    public, headers = fixture()
    with pytest.raises(GatewayIdentityDenied):
        verify(public, headers, context=context, once=lambda *_: pytest.fail("nonce consumed"))


def test_duplicate_mixed_case_signature_headers_denied():
    public, headers = fixture()
    mixed = headers + [(b"X-ASSISTX-GATEWAY-SIGNATURE", headers[1][1])]
    with pytest.raises(GatewayIdentityDenied):
        verify(public, mixed, once=lambda *_: pytest.fail("nonce consumed"))


@pytest.mark.parametrize("mutator", [
    lambda h: [(h[0][0], h[0][1] + b"="), h[1]],
    lambda h: [(h[0][0], h[0][1] + b"!"), h[1]],
    lambda h: [h[0], (h[1][0], b"a" * 300)],
    lambda h: [h[0], (h[1][0], b"a")],
])
def test_malformed_or_noncanonical_base64_denied(mutator):
    public, headers = fixture()
    with pytest.raises(GatewayIdentityDenied):
        verify(public, mutator(headers), once=lambda *_: pytest.fail("nonce consumed"))


def test_wrong_signer_or_missing_replay_store_denied():
    public, headers = fixture()
    foreign, _ = fixture()
    with pytest.raises(GatewayIdentityDenied):
        verify(foreign, headers, once=lambda *_: pytest.fail("nonce consumed"))
    with pytest.raises(GatewayIdentityDenied):
        verify(public, headers, once=lambda *_: False)
    with pytest.raises(GatewayIdentityDenied):
        verify(public, headers, once=lambda *_: 1)
    with pytest.raises(GatewayIdentityDenied):
        verify(public, headers, once=lambda *_: (_ for _ in ()).throw(ConnectionError()))


def test_synthetic_fastapi_route_only_accepts_signed_one_time_identity():
    public, headers = fixture()
    used = set()
    app = FastAPI()

    @app.get(PATH)
    def observations(request: Request):
        def consume(subject, nonce, expiration):
            token = subject, nonce
            if token in used:
                return False
            used.add(token)
            return True
        try:
            principal = verify_observation_asgi_identity(
                scope=request.scope,
                headers=request.scope["headers"],
                receiver_public_key=public,
                receiver_key_id=KEY_ID,
                now_ms=NOW,
                consume_nonce_once=consume,
            )
        except GatewayIdentityDenied:
            raise HTTPException(status_code=401, detail="signed identity required")
        assert principal == SUBJECT
        return {"authority": "not_admitted", "models": [], "selectable": False}

    client = TestClient(app)
    assert client.get(PATH).status_code == 401
    assert client.get(PATH, headers={"Tailscale-User-Login": SUBJECT}).status_code == 401
    credentials = {k.decode("ascii"): v.decode("ascii") for k,v in headers}
    response = client.get(PATH, headers=credentials)
    assert response.status_code == 200
    assert response.json()["authority"] == "not_admitted"
    assert response.json()["models"] == []
    assert client.get(PATH, headers=credentials).status_code == 401


def test_module_not_wired_into_any_production_mobile_or_backend_route():
    root = Path(__file__).resolve().parents[1]
    for filename in ("src/assistx/api.py", "src/assistx/mobile_agent_routes.py"):
        contents = (root / filename).read_text()
        assert "signed_observation_asgi_research" not in contents
