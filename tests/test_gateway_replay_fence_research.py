"""Offline, synthetic-only receiver-owned signed-gateway replay acceptance.

No Redis service, live user credentials, real signing material or API routes.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from threading import Lock, Thread

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


SRC = Path(__file__).resolve().parents[1] / "src/assistx"
def module(name: str):
    spec = importlib.util.spec_from_file_location(name, SRC / (name + ".py"))
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result

gate = module("trusted_gateway_identity_contract")
fence = module("gateway_replay_fence_research")
NOW = 1_800_000_000_000
KEY = "synthetic-only-gateway-nonce-custody-key-012345"
BOOT = "a" * 40


class FakeRedis:
    def __init__(self):
        self.run_id = BOOT
        self.seen = set()
        self.lock = Lock()
        self.error_info = False
        self.error_eval = False
        self.mutate_run_id = False
        self.invalid_ack = None
        self.saved_keys = []

    def info(self, section="server"):
        if self.error_info:
            raise ConnectionError("synthetic outage")
        assert section == "server"
        return {"run_id": self.run_id}

    def eval(self, lua, key_count, key, expiration):
        assert lua == fence.CONSUME_NONCE_LUA and key_count == 1
        self.saved_keys.append(key)
        if self.error_eval:
            raise ConnectionError("synthetic Redis unavailable")
        if self.mutate_run_id:
            self.run_id = "b" * 40
        if self.invalid_ack is not None:
            return self.invalid_ack
        if expiration <= NOW or expiration - NOW > fence.MAX_TTL_MS:
            return -1
        with self.lock:
            if key in self.seen:
                return 0
            self.seen.add(key)
            return 1


def valid_signed_fixture():
    claim = {
        "version": gate.VERSION, "issuer": gate.ISSUER,
        "audience": gate.AUDIENCE, "key_id": "synthetic-gateway",
        "subject": "synthetic-phone@example.invalid",
        "issued_at_ms": NOW - 100, "expires_at_ms": NOW + 20000,
        "nonce": "d" * 32, "method": "GET",
        "target": "/api/traces?limit=1", "scope": "trace:read",
    }
    private = Ed25519PrivateKey.generate()
    message = gate.canonical_claim(claim)
    signature = private.sign(gate.PREFIX + message)
    pubkey = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return message, signature, pubkey, claim


def verify(redis_client, *, mutate=None, **overrides):
    message, signature, key, claim = valid_signed_fixture()
    if mutate is not None:
        claim.update(mutate)
        # Separate disposable, test-only signer. NEVER a production gateway.
        keypair = Ed25519PrivateKey.generate()
        message = gate.canonical_claim(claim)
        signature = keypair.sign(gate.PREFIX + message)
        key = keypair.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    approved_store = fence.GatewayReplayFence(redis_client, KEY, BOOT)
    args = dict(
        claim_bytes=message, signature=signature, receiver_public_key=key,
        receiver_key_id="synthetic-gateway",
        now_ms=NOW, request_method="GET",
        request_target="/api/traces?limit=1",
        required_scope="trace:read",
        consume_nonce_once=approved_store.consume,
    )
    args.update(overrides)
    return gate.verify_gateway_assertion(**args)


def test_signed_phone_style_request_has_no_basic_auth_and_one_use():
    db = FakeRedis()
    # A phone request authenticated by a future ingress must not carry Basic.
    phone_headers = {"Accept": "application/json"}
    assert "Authorization" not in phone_headers
    assert verify(db) == "synthetic-phone@example.invalid"
    with pytest.raises(gate.GatewayIdentityDenied):
        verify(db)
    assert db.saved_keys
    assert all("synthetic-phone" not in key for key in db.saved_keys)


def test_forged_raw_identity_header_is_never_an_authorization():
    db = FakeRedis()
    untrusted_headers = {"Tailscale-User-Login": "spoof@example.invalid"}
    assert untrusted_headers["Tailscale-User-Login"] != "synthetic-phone@example.invalid"
    message, _, pubkey, claim = valid_signed_fixture()
    with pytest.raises(gate.GatewayIdentityDenied):
        gate.verify_gateway_assertion(
            claim_bytes=message, signature=b"", receiver_public_key=pubkey,
            receiver_key_id="synthetic-gateway", now_ms=NOW,
            request_method="GET", request_target=claim["target"],
            required_scope=claim["scope"],
            consume_nonce_once=fence.GatewayReplayFence(db, KEY, BOOT).consume,
        )
    assert db.saved_keys == []


def test_target_scope_and_expiry_mismatch_do_not_touch_replay_store():
    db = FakeRedis()
    for kw in ({"request_target": "/api/traces?limit=2"},
               {"request_method": "POST"},
               {"required_scope": "agent:chat"},
               {"now_ms": NOW + 25_000}):
        with pytest.raises(gate.GatewayIdentityDenied):
            verify(db, **kw)
    assert db.saved_keys == []


@pytest.mark.parametrize("field,value", [
    ("error_info", True), ("error_eval", True), ("mutate_run_id", True),
    ("invalid_ack", "1"), ("invalid_ack", True), ("invalid_ack", 2),
])
def test_redis_failure_or_ambiguous_ack_never_grants_operator(field, value):
    db = FakeRedis()
    setattr(db, field, value)
    with pytest.raises(gate.GatewayIdentityDenied):
        verify(db)


@pytest.mark.parametrize("bad_pin", ["b" * 40, "not-a-pin", None])
def test_stale_or_unpinned_redis_generation_denies(bad_pin):
    db = FakeRedis()
    replay = fence.GatewayReplayFence(db, KEY, bad_pin)
    with pytest.raises(fence.GatewayReplayUnavailable):
        replay.consume("synthetic-phone@example.invalid", "d" * 32, NOW + 10000)


@pytest.mark.parametrize("subject,nonce,expiry", [
    ("invalid", "d" * 32, NOW + 1000),
    ("synthetic-phone@example.invalid", "short", NOW + 1000),
    ("synthetic-phone@example.invalid", "d" * 32, True),
    ("synthetic-phone@example.invalid", "d" * 32, -1),
])
def test_malformed_nonce_context_denied_without_store_write(subject, nonce, expiry):
    db = FakeRedis()
    with pytest.raises(fence.GatewayReplayUnavailable):
        fence.GatewayReplayFence(db, KEY, BOOT).consume(subject, nonce, expiry)
    assert db.saved_keys == []


def test_atomic_concurrent_nonce_accepts_exactly_once():
    db = FakeRedis()
    store = fence.GatewayReplayFence(db, KEY, BOOT)
    outcomes = []
    threads = [
        Thread(target=lambda: outcomes.append(
            store.consume("synthetic-phone@example.invalid", "e" * 32, NOW + 1000)
        )) for _ in range(10)
    ]
    for th in threads: th.start()
    for th in threads: th.join(timeout=2)
    assert len(outcomes) == 10
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 9


def test_no_application_route_imports_replay_fence():
    for name in ("api.py", "swarm_routes.py"):
        assert "gateway_replay_fence_research" not in (SRC / name).read_text()
