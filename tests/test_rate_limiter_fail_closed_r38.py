"""R38: the rate limiter must fail closed when its backend cannot be checked.

The middleware in ``assistx.api`` treats ``allowed=False`` as HTTP 429. A backend
error must therefore never be reported as ``allowed=True``: that silently turns
"the counter could not be read" into an unlimited-request green light on
/dispatch, /ask, /intents and /paperclip/events.
"""

from __future__ import annotations

import pytest

from assistx import rate_limiter


@pytest.fixture
def limiter(monkeypatch):
    monkeypatch.setattr(rate_limiter, "_r", None)
    return rate_limiter.RateLimiter("r38-probe", max_requests=2, window_seconds=60)


def test_backend_connect_error_fails_closed(monkeypatch, limiter):
    def boom():
        raise rate_limiter.redis_module.RedisError("backend unavailable")

    monkeypatch.setattr(rate_limiter, "_get_redis", boom)

    allowed, remaining, retry_after = limiter.check("client-connect")

    assert allowed is False
    assert remaining == 0
    assert retry_after >= 1


def test_backend_pipeline_error_fails_closed(monkeypatch, limiter):
    class BrokenBackend:
        def pipeline(self, transaction=True):
            raise rate_limiter.redis_module.RedisError("pipeline unavailable")

    monkeypatch.setattr(rate_limiter, "_get_redis", lambda: BrokenBackend())

    allowed, remaining, retry_after = limiter.check("client-pipeline")

    assert allowed is False
    assert remaining == 0
    assert retry_after >= 1


def test_healthy_backend_still_enforces_the_limit(monkeypatch, limiter):
    backend = rate_limiter.redis_module.from_url("memory://r38-rate-limit")
    backend.delete("ratelimit:r38-probe:client-healthy")
    monkeypatch.setattr(rate_limiter, "_get_redis", lambda: backend)

    first = limiter.check("client-healthy")
    second = limiter.check("client-healthy")
    third = limiter.check("client-healthy")

    assert first == (True, 1, 0)
    assert second == (True, 0, 0)
    assert third[0] is False
    assert third[1] == 0
    assert third[2] >= 1
