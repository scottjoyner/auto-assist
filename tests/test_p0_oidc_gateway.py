"""P0 #37 verified human gateway: pinned IdP claims, never Basic/header identities.

Uses disposable RSA IdP and Ed25519 gateway keys and HTTPX MockTransport.
No real IdP session, router provider, model, NAS, or production service used.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Avoid importing AssistX's unrelated heavy Neo4j/LLM package bootstrap
# in this isolated security boundary test. The production API imports the
# module normally only after its explicit feature flag is enabled.
_module_file = Path(__file__).resolve().parents[1] / "src/assistx/p0_oidc_gateway.py"
_spec = spec_from_file_location("p0_oidc_test_subject", _module_file)
_gateway_module = module_from_spec(_spec)
_spec.loader.exec_module(_gateway_module)
HumanGatewayDenied = _gateway_module.HumanGatewayDenied
VerifiedHumanGateway = _gateway_module.VerifiedHumanGateway
install_verified_human_gateway = _gateway_module.install_verified_human_gateway


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _oidc(private, sub: str, *, changes=None, kid="idp-key"):
    now = int(time.time())
    claims = {
        "iss": "https://idp.example.test",
        "aud": "assistx-human-gateway",
        "sub": sub,
        "iat": now,
        "nbf": now,
        "exp": now + 600,
    }
    if changes:
        claims.update(changes)
    header = {"alg": "RS256", "kid": kid, "typ": "JWT"}
    text = ".".join([
        _b64(json.dumps(header, sort_keys=True).encode()),
        _b64(json.dumps(claims, sort_keys=True).encode()),
    ])
    signature = private.sign(
        text.encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    return text + "." + _b64(signature)


def _setup():
    idp_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = idp_private.public_key().public_numbers()
    jwks = {"keys": [{
        "kid": "idp-key",
        "kty": "RSA",
        "alg": "RS256",
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length()+7)//8, "big")),
        "e": _b64(numbers.e.to_bytes((numbers.e.bit_length()+7)//8, "big")),
    }]}
    roster = {
        "issuer": "https://idp.example.test",
        "users": [
            {
                "sub": "idp-alice", "kind": "human", "tenant": "shared",
                "subject": "alice", "models": ["auto/code"],
            },
            {
                "sub": "idp-bob", "kind": "human", "tenant": "shared",
                "subject": "bob", "models": ["auto/code"],
            },
        ],
    }
    signer = Ed25519PrivateKey.generate()
    gateway = VerifiedHumanGateway(
        jwks=jwks,
        roster=roster,
        gateway_key=signer,
        gateway_kid="fixture-router-signing-key",
        router_service_bearer="fixture-private-router-service-key",
        router_url="https://router.example.test/v1/chat/completions",
        audience="assistx-human-gateway",
    )
    return idp_private, signer, gateway, jwks, roster


def _delegated_claims(token, signer):
    header, body, signature = token.split(".")
    signer.public_key().verify(
        base64.urlsafe_b64decode(signature + "=" * ((-len(signature)) % 4)),
        (header + "." + body).encode("ascii"),
    )
    return json.loads(
        base64.urlsafe_b64decode(body + "=" * ((-len(body)) % 4))
    )


def test_two_pinned_idp_subjects_get_separate_gateway_signed_attempts():
    idp, signer, gateway, _, _ = _setup()
    token_a, a = gateway.issue(_oidc(idp, "idp-alice"), "auto/code")
    token_b, b = gateway.issue(_oidc(idp, "idp-bob"), "auto/code")
    assert (a["tenant"], a["subject"]) == ("shared", "alice")
    assert (b["tenant"], b["subject"]) == ("shared", "bob")
    assert a["root_request_id"] != b["root_request_id"]
    assert a["attempt_id"] != b["attempt_id"]
    assert a["jti"] != b["jti"]
    assert _delegated_claims(token_a, signer) == a
    assert _delegated_claims(token_b, signer) == b
    assert a["aud"] == b["aud"] == "auto-router-p0-shadow-user"
    assert a["exp"] - a["iat"] == 60


@pytest.mark.parametrize("change", [
    {"iss": "https://attacker.test"},
    {"aud": "wrong-audience"},
    {"exp": 1},
    {"nbf": int(time.time()) + 3600},
    {"iat": int(time.time()) - 7200},
])
def test_wrong_issuer_audience_expiry_or_time_is_rejected(change):
    idp, _, gateway, _, _ = _setup()
    token = _oidc(idp, "idp-alice", changes=change)
    with pytest.raises(HumanGatewayDenied):
        gateway.issue(token, "auto/code")


def test_unknown_real_sub_is_not_a_client_supplied_user_identity():
    idp, _, gateway, _, _ = _setup()
    with pytest.raises(HumanGatewayDenied):
        gateway.issue(_oidc(idp, "unknown-idp-user"), "auto/code")
    with pytest.raises(HumanGatewayDenied):
        gateway.issue(_oidc(idp, "idp-alice"), "auto/other")
    with pytest.raises(HumanGatewayDenied):
        gateway.issue("unverified-bearer-token", "auto/code")


def test_forged_signer_wrong_kid_and_noncanonical_identity_denied():
    idp, _, gateway, _, _ = _setup()
    forgery = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HumanGatewayDenied):
        gateway.issue(_oidc(forgery, "idp-alice"), "auto/code")
    with pytest.raises(HumanGatewayDenied):
        gateway.issue(_oidc(idp, "idp-alice", kid="unknown"), "auto/code")
    token = _oidc(idp, "idp-alice")
    with pytest.raises(HumanGatewayDenied):
        gateway.issue(token[:-1] + ("A" if token[-1] != "A" else "B"), "auto/code")


def test_gateway_forwards_only_signed_subject_and_server_chosen_headers():
    idp, signer, gateway, _, _ = _setup()
    requests = []

    def router(request: httpx.Request):
        requests.append(request)
        return httpx.Response(
            200, json={"id": "stub", "choices": [{"text": "synthetic"}]},
        )

    transport = httpx.MockTransport(router)
    async def perform():
        return await gateway.forward(
            _oidc(idp, "idp-alice"),
            {
                "model": "auto/code", "messages": [
                    {"role": "user", "content": "hello"}
                ], "max_tokens": 8,
            },
            transport=transport,
        )

    status, body = asyncio.run(perform())
    assert status == 200 and body["id"] == "stub"
    assert len(requests) == 1
    sent = requests[0]
    assert sent.url == "https://router.example.test/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer fixture-private-router-service-key"
    assert _delegated_claims(
        sent.headers["x-auto-router-user-delegation"], signer
    )["subject"] == "alice"
    assert "trusted_auth_header" not in sent.headers
    assert "x-user" not in sent.headers
    routed = json.loads(sent.content)
    assert set(routed) == {"model", "messages", "max_tokens"}
    assert "subject" not in str(routed)
    assert "alice" not in str(body)


def test_untrusted_client_metadata_user_and_stream_control_rejected():
    idp, _, gateway, _, _ = _setup()
    async def perform(payload):
        return await gateway.forward(
            _oidc(idp, "idp-alice"), payload,
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})),
        )
    base = {
        "model": "auto/code",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 8,
    }
    for spoof in (
        {"user": "bob"}, {"metadata": {"subject": "bob"}},
        {"stream": True}, {"tenant": "spoofed"},
    ):
        with pytest.raises(HumanGatewayDenied):
            asyncio.run(perform({**base, **spoof}))
    with pytest.raises(HumanGatewayDenied):
        asyncio.run(perform({**base, "max_tokens": 10000}))


def test_forwarded_router_failure_does_not_silently_fallback():
    idp, _, gateway, _, _ = _setup()
    async def perform():
        return await gateway.forward(
            _oidc(idp, "idp-bob"),
            {
                "model": "auto/code",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 8,
            },
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, json={"detail": "offline"})
            ),
        )
    code, payload = asyncio.run(perform())
    assert code == 503 and payload["detail"] == "offline"


def test_gateway_default_off_does_not_mount_route(monkeypatch):
    monkeypatch.delenv("ASSISTX_P0_VERIFIED_HUMAN_GATEWAY_ENABLED", raising=False)
    app = FastAPI()
    install_verified_human_gateway(app)
    assert "/api/p0/verified-local-chat" not in {getattr(r, "path", None) for r in app.routes}


def test_gateway_refuses_enabled_missing_pinned_identity_files(monkeypatch):
    monkeypatch.setenv("ASSISTX_P0_VERIFIED_HUMAN_GATEWAY_ENABLED", "true")
    for name in (
        "ASSISTX_P0_IDP_JWKS_FILE", "ASSISTX_P0_HUMAN_ROSTER_FILE",
        "ASSISTX_P0_ROUTER_SIGNING_KEY_FILE",
        "ASSISTX_P0_ROUTER_SERVICE_BEARER_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="unsafe startup"):
        install_verified_human_gateway(FastAPI())


def test_enabled_gateway_installer_requires_private_files_and_mounts_only_new_lane(
    monkeypatch, tmp_path,
):
    idp, signer, gateway, jwks, roster = _setup()
    values = {
        "ASSISTX_P0_IDP_JWKS_FILE": json.dumps(jwks).encode(),
        "ASSISTX_P0_HUMAN_ROSTER_FILE": json.dumps(roster).encode(),
        "ASSISTX_P0_ROUTER_SIGNING_KEY_FILE": signer.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        "ASSISTX_P0_ROUTER_SERVICE_BEARER_FILE": (
            b"fixture-private-router-service-key"
        ),
    }
    monkeypatch.setenv("ASSISTX_P0_VERIFIED_HUMAN_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("ASSISTX_P0_ROUTER_SIGNING_KID", "fixture-router-signing-key")
    monkeypatch.setenv("ASSISTX_P0_IDP_AUDIENCE", "assistx-human-gateway")
    monkeypatch.setenv(
        "ASSISTX_P0_ROUTER_URL",
        "https://router.example.test/v1/chat/completions",
    )
    for key, content in values.items():
        file = tmp_path / key
        file.write_bytes(content)
        file.chmod(0o600)
        monkeypatch.setenv(key, str(file))
    app = FastAPI()
    install_verified_human_gateway(app)
    # The bearer must be an independently verified JWT, not Basic nor a
    # spoofed trusted-user header. No provider request is made here.
    with TestClient(app) as client:
        response = client.post(
            "/api/p0/verified-local-chat",
            headers={"X-Trusted-User": "alice"},
            json={"model": "auto/code", "messages": [], "max_tokens": 8},
        )
    assert response.status_code == 401
