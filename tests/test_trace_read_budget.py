"""Synthetic Redis EVAL admission-contract tests; no Redis network or host traffic."""
from __future__ import annotations

import pytest

from assistx.trace_read_budget import (
    TraceReadBudgetUnavailable,
    check_trace_read_budget,
)


class FakeRedis:
    def __init__(self, answer=(1, 44, 0), error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def eval(self, script, key_count, *argv):
        self.calls.append((script, key_count, argv))
        if self.error:
            raise self.error
        return self.answer


def test_redis_transaction_is_single_eval_with_server_clock_and_hashed_principal():
    fake = FakeRedis()
    allowed, remaining, retry = check_trace_read_budget(
        "operator:test", redis_client=fake, key_secret="synthetic-only-key-not-real", max_requests=45, window_seconds=60
    )
    assert (allowed, remaining, retry) == (True, 44, 0)
    assert len(fake.calls) == 1
    script, key_count, argv = fake.calls[0]
    assert key_count == 1
    assert len(argv) == 4  # key + 3 Lua arguments
    assert argv[0].startswith("ratelimit:trace-read:v1:")
    assert "operator:test" not in str(fake.calls)
    assert "correlation_id" not in str(fake.calls)
    assert "redis.call('TIME')" in script
    assert "redis.call('ZREMRANGEBYSCORE'" in script
    assert "redis.call('ZCARD'" in script
    assert "redis.call('ZADD'" in script
    assert "redis.call('PEXPIRE'" in script
    assert argv[1:3] == (60000, 45)


def test_quota_key_stable_by_operator_but_not_shared_across_principals():
    fake = FakeRedis()
    check_trace_read_budget("operator-a", redis_client=fake, key_secret="synthetic-only-key-not-real")
    check_trace_read_budget("operator-a", redis_client=fake, key_secret="synthetic-only-key-not-real")
    check_trace_read_budget("operator-b", redis_client=fake, key_secret="synthetic-only-key-not-real")
    assert fake.calls[0][2][0] == fake.calls[1][2][0]
    assert fake.calls[0][2][0] != fake.calls[2][2][0]
    assert fake.calls[0][2][-1] != fake.calls[1][2][-1]


def test_denied_bucket_returns_429_contract_and_retry_after():
    fake = FakeRedis((0, 0, 7))
    assert check_trace_read_budget("fixture", redis_client=fake, key_secret="synthetic-only-key-not-real") == (False, 0, 7)


@pytest.mark.parametrize("bad", [(1, -1, 0), (0, 1, 4), (0, 0, 0), (1, 2, 3),
                                  (2, 0, 0), (1, 45, -1), (0, 0, 61),
                                  (True,), [], None, "allowed"])
def test_invalid_redis_result_fails_closed(bad):
    fake = FakeRedis(bad)
    with pytest.raises(TraceReadBudgetUnavailable):
        check_trace_read_budget("fixture", redis_client=fake, key_secret="synthetic-only-key-not-real")


@pytest.mark.parametrize("failure", [ConnectionError("Redis disconnected"),
                                     TimeoutError("Redis timed out"),
                                     RuntimeError("eval unsupported")])
def test_redis_storage_or_eval_error_never_grants_admission(failure):
    fake = FakeRedis(error=failure)
    with pytest.raises(TraceReadBudgetUnavailable, match="cannot be verified"):
        check_trace_read_budget("fixture", redis_client=fake, key_secret="synthetic-only-key-not-real")


@pytest.mark.parametrize("kwargs", [
    {"principal": ""},
    {"principal": None},
    {"principal": "fixture", "max_requests": 0},
    {"principal": "fixture", "max_requests": True},
    {"principal": "fixture", "window_seconds": 0},
    {"principal": "fixture", "window_seconds": 90000},
])
def test_invalid_identity_or_meter_configuration_fails_closed_before_redis(kwargs):
    fake = FakeRedis()
    with pytest.raises(TraceReadBudgetUnavailable):
        check_trace_read_budget(redis_client=fake, key_secret="synthetic-only-key-not-real", **kwargs)
    assert fake.calls == []


def test_missing_or_short_key_custody_fails_closed_before_redis(monkeypatch):
    monkeypatch.delenv("ASSISTX_TRACE_READ_BUDGET_KEY_SECRET", raising=False)
    fake = FakeRedis()
    with pytest.raises(TraceReadBudgetUnavailable, match="key custody"):
        check_trace_read_budget("fixture", redis_client=fake)
    with pytest.raises(TraceReadBudgetUnavailable, match="key custody"):
        check_trace_read_budget("fixture", redis_client=fake, key_secret="short")
    assert fake.calls == []


def test_hmac_key_rotation_changes_redis_namespace_without_exposing_secret():
    fake = FakeRedis()
    check_trace_read_budget("operator-a", redis_client=fake, key_secret="synthetic-rotating-key-one")
    check_trace_read_budget("operator-a", redis_client=fake, key_secret="synthetic-rotating-key-two")
    assert fake.calls[0][2][0] != fake.calls[1][2][0]
    assert "synthetic-rotating-key-one" not in str(fake.calls)
    assert "synthetic-rotating-key-two" not in str(fake.calls)
