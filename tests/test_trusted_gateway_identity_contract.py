"""Offline, synthetic-only signed Tailnet identity receiver contract.

No actual Tailscale sessions, keys, user records, Redis, Neo4j or live routes.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MODULE = (Path(__file__).resolve().parents[1] /
          "src/assistx/trusted_gateway_identity_contract.py")
spec = importlib.util.spec_from_file_location("trusted_gateway_identity_contract", MODULE)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

NOW = 1_800_000_000_000


def _claim(**edits):
    result = {
        "version": gate.VERSION, "issuer": gate.ISSUER,
        "audience": gate.AUDIENCE, "key_id": "synthetic-gateway-kid-1",
        "subject": "synthetic-operator@example.invalid",
        "issued_at_ms": NOW - 1_000, "expires_at_ms": NOW + 10_000,
        "nonce": "a" * 32, "method": "GET",
        "target": "/api/traces?limit=50",
        "scope": "trace:read",
    }
    result.update(edits)
    return result


def _signed(claim):
    key = Ed25519PrivateKey.generate()
    blob = gate.canonical_claim(claim)
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return blob, key.sign(gate.PREFIX + blob), public


def _verify(blob, signature, pubkey, consume, **edits):
    args = {
        "claim_bytes": blob, "signature": signature,
        "receiver_public_key": pubkey,
        "receiver_key_id": "synthetic-gateway-kid-1",
        "now_ms": NOW, "request_method": "GET",
        "request_target": "/api/traces?limit=50",
        "required_scope": "trace:read", "consume_nonce_once": consume,
    }
    args.update(edits)
    return gate.verify_gateway_assertion(**args)


def _atomic_mock():
    seen = set()
    def first(subject, nonce, expires):
        key = subject, nonce
        if key in seen:
            return False
        seen.add(key)
        return True
    return first


def test_correct_signature_and_exact_request_only_once():
    blob, signature, key = _signed(_claim())
    store = _atomic_mock()
    assert _verify(blob, signature, key, store) == "synthetic-operator@example.invalid"
    with pytest.raises(gate.GatewayIdentityDenied):
        _verify(blob, signature, key, store)


@pytest.mark.parametrize("edits", [
    {"version": "other"},
    {"issuer": "an-untrusted-container"},
    {"audience": "another-service"},
    {"key_id": "other-key"},
    {"subject": "malformed-principal"},
    {"nonce": "short"},
    {"issued_at_ms": NOW + 4000},
    {"expires_at_ms": NOW},
    {"expires_at_ms": NOW + 31_000},
    {"issued_at_ms": True},
    {"scope": "trace:write"},
    {"target": "/api/traces?limit=500"},
    {"method": "POST"},
])
def test_disallowed_claims_never_consume_replay_nonce(edits):
    blob, signature, key = _signed(_claim(**edits))
    touched = []
    with pytest.raises(gate.GatewayIdentityDenied):
        _verify(blob, signature, key,
                lambda *a: touched.append(True) or True)
    assert not touched


def test_changed_method_path_scope_or_pinned_key_deny_before_nonce():
    blob, signature, key = _signed(_claim())
    for kwargs in (
        {"request_method": "POST"},
        {"request_target": "/api/traces?limit=51"},
        {"required_scope": "agent:chat"},
        {"receiver_key_id": "other-kid"},
        {"receiver_public_key": b"\x11" * 32},
    ):
        touched = []
        with pytest.raises(gate.GatewayIdentityDenied):
            _verify(blob, signature, key,
                    lambda *a: touched.append(True) or True, **kwargs)
        assert not touched


def test_signature_mutation_denied_before_nonce():
    blob, sig, key = _signed(_claim())
    mutated = bytes([sig[0] ^ 1]) + sig[1:]
    with pytest.raises(gate.GatewayIdentityDenied):
        _verify(blob, mutated, key, lambda *a: pytest.fail("nonce consumed"))


def test_noncanonical_json_or_duplicate_fields_denied():
    blob, sig, key = _signed(_claim())
    for mutated in (
        blob.replace(b'"scope":"trace:read"', b'"scope": "trace:read"'),
        b'{"scope":"trace:read","scope":"trace:read"}',
        b'null',
        b'{"subject":NaN}',
    ):
        with pytest.raises(gate.GatewayIdentityDenied):
            _verify(mutated, sig, key,
                    lambda *a: pytest.fail("bad claim consumed nonce"))


def test_redis_or_nonce_store_outage_never_grants_identity():
    blob, sig, key = _signed(_claim())
    def broken(*_):
        raise ConnectionError("synthetic Redis generation unavailable")
    with pytest.raises(gate.GatewayIdentityDenied):
        _verify(blob, sig, key, broken)
    with pytest.raises(gate.GatewayIdentityDenied):
        _verify(blob, sig, key, lambda *_: 1)  # must be literal True
    with pytest.raises(gate.GatewayIdentityDenied):
        _verify(blob, sig, key, lambda *_: False)


def test_unsigned_or_wrong_length_or_unpinned_key_denied():
    blob, sig, key = _signed(_claim())
    for changed in (
        {"signature": b""},
        {"signature": sig[:20]},
        {"receiver_public_key": b"short"},
        {"now_ms": True},
        {"claim_bytes": blob * 3},
    ):
        with pytest.raises(gate.GatewayIdentityDenied):
            _verify(blob, sig, key,
                    lambda *a: pytest.fail("unverified nonce consumed"), **changed)


def test_module_is_not_wired_to_fastapi_routes():
    root = MODULE.parents[2]
    for filename in ("src/assistx/api.py", "src/assistx/swarm_routes.py"):
        source = (root / filename).read_text(encoding="utf8")
        assert "trusted_gateway_identity_contract" not in source
