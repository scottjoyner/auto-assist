"""Hermetic regression: /api/ask idempotency keys must be principal-scoped.

The Redis-backed idempotency store is keyed only by the client-supplied
``idempotency_key``. Two authenticated principals (or the single-user basic-auth
principal and an executor identity injected via TRUSTED_AUTH_HEADER) that reuse
the same key therefore collide: the second caller receives the first caller's
answer_id / sync_result. The graph-level owner fence
(``owner_identity.scoped_key``) does not cover this Redis fast path.

No live Redis, Neo4j, HTTP endpoint, credential file or network mount is used:
``ASSISTX_DEPENDENCY_MODE=compat`` (pinned in tests/conftest.py) makes the store
use the in-process InMemoryRedis shim, which we replace with a local fake.
"""
from __future__ import annotations

import pytest

from assistx import idempotency_store as store


class _FakeRedis:
    """Minimal setex/get fake; no sockets and no TTL machinery needed here."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.kv[key] = value

    def get(self, key: str) -> str | None:
        return self.kv.get(key)


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(store, "_r", fake)
    return fake


def _save(principal: str, key: str, record: dict) -> None:
    """Use the scoped API when present; otherwise reproduce the base behavior."""
    scoped = getattr(store, "save_for", None)
    if scoped is None:  # base: unscoped raw-key save
        store.save(key, record)
        return
    scoped(principal, key, record)


def _load(principal: str, key: str):
    scoped = getattr(store, "load_for", None)
    if scoped is None:  # base: unscoped raw-key load
        return store.load(key)
    return scoped(principal, key)


def test_cross_principal_idempotency_does_not_leak(fake_redis: _FakeRedis) -> None:
    _save("alice", "shared-key", {"answer_id": "alice-answer"})
    # A different principal reusing the same client key must not see alice's answer.
    assert _load("bob", "shared-key") is None


def test_same_principal_same_key_roundtrips(fake_redis: _FakeRedis) -> None:
    record = {"answer_id": "ans-1"}
    _save("alice", "k1", record)
    assert _load("alice", "k1") == record


def test_distinct_keys_isolated_within_principal(fake_redis: _FakeRedis) -> None:
    _save("alice", "k1", {"answer_id": "ans-1"})
    assert _load("alice", "k2") is None


def test_principal_key_is_stable_domain_separated_and_non_leaking() -> None:
    principal_key = getattr(store, "principal_key", None)
    assert principal_key is not None, "principal_key helper is missing"
    a = principal_key("alice", "shared-key")
    assert a == principal_key("alice", "shared-key")
    assert a != principal_key("bob", "shared-key")
    assert a != principal_key("alice", "other-key")
    assert "alice" not in a and "shared-key" not in a
    assert len(a) == 64
