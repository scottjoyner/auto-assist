"""Contract-only fixtures. Never connect to a real Redis instance or API."""
from __future__ import annotations

import dataclasses
import re

import pytest

from assistx import trace_index_fenced_research as subject

KEY = "synthetic-test-receiver-owned-key-32-characters-minimum"


_DEFAULT = object()


class FakeRedis:
    def __init__(self, answer=_DEFAULT, fail=None):
        self.answer = [1, 0, 0] if answer is _DEFAULT else answer
        self.fail = fail
        self.calls = []

    def eval(self, script, count, *args):
        self.calls.append((script, count, args))
        if self.fail:
            raise self.fail
        if script == subject.ACQUIRE_LUA:
            return self.answer
        return 1


def test_single_atomic_eval_hmac_key_tags_no_raw_identity_or_payload():
    client = FakeRedis()
    decision = subject.acquire(client, "synthetic-person", KEY)
    assert decision.allowed and decision.lease and decision.reason == "admitted"
    script, count, args = client.calls[0]
    assert script == subject.ACQUIRE_LUA and count == 4
    assert all("{assistx-trace-index-v2}" in key for key in args[:4])
    assert "synthetic-person" not in str(args)
    assert KEY not in str(args)
    assert len(args) == 11
    assert args[4:10] == (12, 60, 60000, 2, 3, 15000)
    assert re.fullmatch(r"[0-9a-f]{32}", args[-1])
    assert decision.lease.nonce == args[-1]
    assert "redis.call('TIME')" in script
    assert "redis.call('ZADD'" in script


def test_operator_identity_is_hmac_scoped_but_global_keys_shared():
    a = subject._keys("synthetic-a", KEY)
    b = subject._keys("synthetic-b", KEY)
    assert a[0] != b[0] and a[2] != b[2]
    assert a[1] == b[1] and a[3] == b[3]
    assert subject._keys("synthetic-a", "another-synthetic-receiver-secret-unique") != a


@pytest.mark.parametrize("answer,reason", [
    ([0, 1, 11], "principal_rate"),
    ([0, 2, 11], "fleet_rate"),
    ([0, 3, 11], "principal_inflight"),
    ([0, 4, 11], "fleet_inflight"),
    ([0, 5, 1], "nonce_replay"),
])
def test_bounded_denials_do_not_issue_a_lease(answer, reason):
    decision = subject.acquire(FakeRedis(answer), "synthetic-a", KEY)
    assert not decision.allowed and decision.reason == reason
    assert decision.retry_after_seconds == answer[2]
    assert decision.lease is None


@pytest.mark.parametrize("raw", [[], None, [1], [1, 0], [True, 0, 0],
                                  [1, 0, 2], [1, 4, 0], [0, 0, 3],
                                  [0, 1, 0], [0, 8, 5], [0, 1, -1],
                                  [0, 1, 100000], ["1", 0, 0]])
def test_malformed_reply_never_grants_a_lease(raw):
    with pytest.raises(subject.AdmissionUnavailable):
        subject.acquire(FakeRedis(raw), "synthetic-a", KEY)


@pytest.mark.parametrize("failure", [ConnectionError("shared Redis missing"),
                                      TimeoutError("timeout"), RuntimeError("eval denied")])
def test_redis_errors_are_fail_closed(failure):
    with pytest.raises(subject.AdmissionUnavailable, match="store unavailable"):
        subject.acquire(FakeRedis(fail=failure), "synthetic-a", KEY)


@pytest.mark.parametrize("principal,secret,policy", [
    ("", KEY, subject.Policy()), (None, KEY, subject.Policy()),
    ("synthetic", "short", subject.Policy()),
    ("synthetic", KEY, subject.Policy(principal_rate=0)),
    ("synthetic", KEY, subject.Policy(principal_rate=True)),
    ("synthetic", KEY, subject.Policy(principal_inflight=5, fleet_inflight=3)),
    ("synthetic", KEY, subject.Policy(principal_rate=61, fleet_rate=60)),
])
def test_bad_identity_custody_or_policy_rejected_before_redis(principal, secret, policy):
    client = FakeRedis()
    with pytest.raises(subject.AdmissionUnavailable):
        subject.acquire(client, principal, secret, policy)
    assert not client.calls


def test_caller_supplied_nonce_must_be_128_bits_hex():
    client = FakeRedis()
    with pytest.raises(subject.AdmissionUnavailable):
        subject.acquire(client, "fixture", KEY, nonce="operator-controlled-guess")
    assert not client.calls


def test_release_and_renew_use_only_current_exact_nonce_both_keys():
    client = FakeRedis()
    granted = subject.acquire(client, "fixture", KEY)
    assert granted.lease
    assert subject.renew(client, granted.lease)
    assert subject.release(client, granted.lease)
    assert client.calls[1][0] == subject.RENEW_LUA
    assert client.calls[2][0] == subject.RELEASE_LUA
    assert client.calls[1][1] == client.calls[2][1] == 2
    assert client.calls[2][2][-1] == granted.lease.nonce
    assert "ZSCORE" in subject.RELEASE_LUA and "ZREM" in subject.RELEASE_LUA
    assert "ZSCORE" in subject.RENEW_LUA


def test_stale_release_or_renew_returns_false_not_success():
    client = FakeRedis()
    decision = subject.acquire(client, "fixture", KEY)
    client.eval = lambda *args: 0
    assert subject.release(client, decision.lease) is False
    assert subject.renew(client, decision.lease) is False


def test_bad_acknowledgments_are_not_success():
    client = FakeRedis()
    lease = subject.acquire(client, "fixture", KEY).lease
    for invalid in (None, True, "1", [1], 3):
        client.eval = lambda *args: invalid
        with pytest.raises(subject.AdmissionUnavailable):
            subject.release(client, lease)
        with pytest.raises(subject.AdmissionUnavailable):
            subject.renew(client, lease)


def test_corrupted_lease_namespace_denied_before_redis():
    client = FakeRedis()
    lease = subject.acquire(client, "fixture", KEY).lease
    client.calls.clear()
    with pytest.raises(subject.AdmissionUnavailable):
        subject.release(client, dataclasses.replace(lease, per_active="external-key"))
    assert client.calls == []


def test_module_requires_explicit_call_and_does_not_import_transport():
    import inspect
    source = inspect.getsource(subject)
    assert "from .rate_limiter" not in source
    assert "redis.Redis(" not in source
    assert "httpx." not in source
    assert "dispatch(" not in source
    assert "ASSISTX_TRACE_READ_BUDGET_MODE" not in source


def test_operator_whitespace_does_not_create_extra_quota_identities():
    assert subject._keys("operator", KEY) == subject._keys("  operator  ", KEY)


@pytest.mark.parametrize("bad_nonce", [None, 3, True, "x"])
def test_corrupt_lease_nonce_fails_closed_without_transport(bad_nonce):
    client = FakeRedis()
    lease = subject.acquire(client, "fixture", KEY).lease
    client.calls.clear()
    with pytest.raises(subject.AdmissionUnavailable):
        subject.release(client, dataclasses.replace(lease, nonce=bad_nonce))
    assert not client.calls
