"""Research-only, atomic Redis trace GET quota, disabled unless explicitly enabled.

No I/O at import time. Per authenticated principal, no client-controlled IP,
forwarding header, trace ID, password or event payload appears in Redis keys.
This is an *admission research prototype*, not approval for production use.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from typing import Any


class TraceReadBudgetUnavailable(RuntimeError):
    """Counter backend unavailable or its response cannot be trusted."""


# Redis EVAL runs as one serialized operation across callers sharing the key.
# Use Redis TIME (not per-fleet-node clocks) and one unique member per request.
# A denied request cannot reset/extend the last admitted request's TTL.
_ATOMIC_TRACE_BUDGET_LUA = """
local key = KEYS[1]
local window_ms = tonumber(ARGV[1])
local max_requests = tonumber(ARGV[2])
local member = ARGV[3]
local redis_time = redis.call('TIME')
local now_ms = tonumber(redis_time[1]) * 1000 + math.floor(tonumber(redis_time[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local count = redis.call('ZCARD', key)
if count >= max_requests then
  local earliest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_ms = window_ms
  if #earliest >= 2 then
    retry_ms = window_ms - (now_ms - tonumber(earliest[2]))
  end
  return {0, 0, math.max(1, math.ceil(retry_ms / 1000))}
end
redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms * 2)
return {1, max_requests - count - 1, 0}
"""


def check_trace_read_budget(
    principal: str,
    *,
    redis_client: Any = None,
    key_secret: str | None = None,
    max_requests: int = 45,
    window_seconds: int = 60,
) -> tuple[bool, int, int]:
    """Return (allowed, remaining, retry_after_seconds), or fail closed.

    No direct Redis calls are made unless a caller explicitly invokes this
    function. Only the route dependency's opt-in mode invokes it in AssistX.
    """
    if not isinstance(principal, str) or not principal.strip():
        raise TraceReadBudgetUnavailable("missing authenticated principal")
    if not isinstance(max_requests, int) or isinstance(max_requests, bool) or not (1 <= max_requests <= 10000):
        raise TraceReadBudgetUnavailable("invalid trace budget limit")
    if not isinstance(window_seconds, int) or isinstance(window_seconds, bool) or not (1 <= window_seconds <= 86400):
        raise TraceReadBudgetUnavailable("invalid trace budget window")
    # Non-public, explicitly provisioned pepper prevents offline guessing of
    # an operator name from a Redis quota key. No secret => no admission.
    if key_secret is None:
        key_secret = os.getenv("ASSISTX_TRACE_READ_BUDGET_KEY_SECRET")
    if not isinstance(key_secret, str) or len(key_secret) < 16:
        raise TraceReadBudgetUnavailable("trace budget key custody is not configured")
    try:
        key_hash = hmac.new(
            key_secret.encode("utf-8"),
            ("assistx:trace-read:v1:" + principal).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    except UnicodeError as exc:
        raise TraceReadBudgetUnavailable("trace budget identity cannot be keyed") from exc
    key = "ratelimit:trace-read:v1:" + key_hash
    member = uuid.uuid4().hex
    try:
        if redis_client is None:
            # Lazily load the shared Redis transport; no host/service calls at
            # import or when the runtime switch is disabled.
            from .rate_limiter import _get_redis
            redis_client = _get_redis()
        result = redis_client.eval(_ATOMIC_TRACE_BUDGET_LUA, 1, key, window_seconds * 1000, max_requests, member)
        if not isinstance(result, (list, tuple)) or len(result) != 3:
            raise ValueError("quota response must contain exactly three fields")
        values = tuple(int(v) for v in result)
        admitted, remaining, retry = values
        if admitted not in (0, 1) or not (0 <= remaining <= max_requests) or not (0 <= retry <= window_seconds):
            raise ValueError("invalid quota response values")
        if (admitted == 1 and retry != 0) or (admitted == 0 and (remaining != 0 or retry < 1)):
            raise ValueError("inconsistent quota response")
        return admitted == 1, remaining, retry
    except Exception as exc:
        raise TraceReadBudgetUnavailable("shared trace budget cannot be verified") from exc
