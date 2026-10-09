from __future__ import annotations

import os
import time
import logging
import uuid
from typing import Optional

from .deps import load_redis_module

redis_module = load_redis_module()

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_r: Optional[redis_module.Redis] = None


def _get_redis() -> redis_module.Redis:
    global _r
    if _r is None:
        _r = redis_module.from_url(REDIS_URL)
    return _r


class RateLimiter:
    def __init__(
        self,
        key_prefix: str,
        max_requests: int,
        window_seconds: int,
    ):
        self.key_prefix = key_prefix
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def check(self, identifier: str) -> tuple[bool, int, int]:
        """Returns (allowed, remaining_requests, retry_after_seconds)."""
        key = f"ratelimit:{self.key_prefix}:{identifier}"
        now_ms = int(time.time() * 1000)
        window_ms = self.window_seconds * 1000
        window_start_ms = now_ms - window_ms

        try:
            r = _get_redis()
            pipe = r.pipeline(transaction=True)
            pipe.zremrangebyscore(key, 0, window_start_ms)
            pipe.zcard(key)
            _, count = pipe.execute()

            if count is not None and int(count) >= self.max_requests:
                oldest = r.zrange(key, 0, 0, withscores=True)
                retry_after = 0
                if oldest:
                    oldest_ms = int(oldest[0][1])
                    retry_after = max(1, int((window_ms - (now_ms - oldest_ms) + 999) / 1000))
                return False, 0, retry_after

            member_id = f"{now_ms}:{uuid.uuid4().hex}"
            pipe = r.pipeline(transaction=True)
            pipe.zadd(key, {member_id: now_ms})
            pipe.expire(key, self.window_seconds * 2)
            pipe.execute()
            return True, max(0, self.max_requests - int(count) - 1), 0
        except redis_module.RedisError as e:
            logger.warning("Rate limiter Redis error: %s", e)
            return True, self.max_requests, 0


DISPATCH_LIMITER = RateLimiter("dispatch", max_requests=60, window_seconds=60)
EVENT_LIMITER = RateLimiter("paperclip_event", max_requests=120, window_seconds=60)
ASK_LIMITER = RateLimiter("ask", max_requests=30, window_seconds=60)
INTENT_LIMITER = RateLimiter("intent", max_requests=60, window_seconds=60)


# Trace History global outcome searches have material Neo4j cost (~million
# synthetic DB hits per query). They require atomic, fail-closed admission:
# the existing generic fail-open per-client limiter is inappropriate here.
TRACE_INDEX_LUA = """
local client = KEYS[1]
local fleet = KEYS[2]
local per_max = tonumber(ARGV[1])
local global_max = tonumber(ARGV[2])
local window = tonumber(ARGV[3])
local member = ARGV[4]
local tm = redis.call('TIME')
local now = tonumber(tm[1]) * 1000 + math.floor(tonumber(tm[2]) / 1000)
local cutoff = now - window
redis.call('ZREMRANGEBYSCORE', client, '-inf', cutoff)
redis.call('ZREMRANGEBYSCORE', fleet, '-inf', cutoff)
local local_count = redis.call('ZCARD', client)
local global_count = redis.call('ZCARD', fleet)
if local_count >= per_max or global_count >= global_max then
  local key = local_count >= per_max and client or fleet
  local first = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = 1
  if #first > 1 then
    retry = math.max(1, math.ceil((window - (now - tonumber(first[2]))) / 1000))
  end
  return {0, 0, retry}
end
redis.call('ZADD', client, now, member)
redis.call('ZADD', fleet, now, member)
redis.call('PEXPIRE', client, window * 2)
redis.call('PEXPIRE', fleet, window * 2)
return {1, per_max - local_count - 1, 0}
"""


class TraceIndexLimiter:
    """Bounded atomic, Redis-backed read admission; fail closed on outages.

    Both per-socket-peer and fleet-wide counters update in one Redis script.
    The Redis TIME command avoids producer/node clock skew. This is a
    rate guard, not authentication or true concurrent-query fencing.
    """

    def __init__(self, per_peer=12, global_max=60, window_seconds=60):
        if not (1 <= per_peer <= global_max <= 1000):
            raise ValueError("Unsafe trace-index thresholds")
        if not (1 <= window_seconds <= 3600):
            raise ValueError("Unsafe trace-index window")
        self.per_peer = per_peer
        self.global_max = global_max
        self.window_seconds = window_seconds

    def check(self, peer):
        import hashlib
        if not isinstance(peer, str) or not peer or len(peer) > 256:
            return False, 0, self.window_seconds
        digest = hashlib.sha256(peer.encode("utf-8")).hexdigest()
        # A common hash-tag keeps both Redis Cluster keys in the same slot.
        client_key = "ratelimit:{assistx_trace_index}:peer:" + digest
        global_key = "ratelimit:{assistx_trace_index}:global"
        try:
            raw = _get_redis().eval(
                TRACE_INDEX_LUA,
                2,
                client_key,
                global_key,
                self.per_peer,
                self.global_max,
                self.window_seconds * 1000,
                uuid.uuid4().hex,
            )
            if not isinstance(raw, (list, tuple)) or len(raw) != 3:
                raise ValueError("Bad Redis admission response")
            allowed, remaining, retry = (int(x) for x in raw)
            if allowed not in (0, 1) or remaining < 0 or retry < 0:
                raise ValueError("Invalid Redis admission response")
            return bool(allowed), remaining, retry
        except (redis_module.RedisError, OSError, TypeError, ValueError) as exc:
            logger.warning(
                "Trace-index Redis admission unavailable; denying expensive read: %s",
                type(exc).__name__,
            )
            return False, 0, self.window_seconds


TRACE_INDEX_LIMITER = TraceIndexLimiter()

# Short-lived, deny-only occupancy lease for expensive trace-index GETs.
# A rolling request count does not limit concurrent Neo4j query pressure.
TRACE_INDEX_LEASE_ACQUIRE_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local token = ARGV[3]
local tm = redis.call('TIME')
local now = tonumber(tm[1])*1000 + math.floor(tonumber(tm[2])/1000)
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local in_flight = redis.call('ZCARD', key)
if in_flight >= capacity then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = 1
  if #oldest > 1 then
    retry = math.max(1, math.ceil((tonumber(oldest[2]) - now)/1000))
  end
  return {0, in_flight, retry}
end
if redis.call('ZSCORE', key, token) then
  return {0, in_flight, 1}
end
redis.call('ZADD', key, 'NX', now + ttl, token)
redis.call('PEXPIRE', key, ttl + 5000)
return {1, in_flight + 1, 0}
"""

TRACE_INDEX_LEASE_RELEASE_LUA = """
local key = KEYS[1]
local token = ARGV[1]
return redis.call('ZREM', key, token)
"""


class TraceIndexLeases:
    """Redis-server-time, token-fenced soft in-flight slot admission.

    A TTL protects recovery when workers die. It is not a hard execution
    revocation; a query lasting past TTL can overlap its successor. Actual
    Neo4j timeout/cancellation admission must be proved before activation.
    """

    key = "lease:{assistx_trace_index}:inflight"

    def __init__(self, max_inflight=3, ttl_seconds=30):
        if not (1 <= max_inflight <= 16 and 10 <= ttl_seconds <= 120):
            raise ValueError("Unsafe trace index in-flight limits")
        self.max_inflight = max_inflight
        self.ttl_seconds = ttl_seconds

    def acquire(self):
        token = uuid.uuid4().hex
        try:
            response = _get_redis().eval(
                TRACE_INDEX_LEASE_ACQUIRE_LUA, 1, self.key,
                self.max_inflight, self.ttl_seconds * 1000, token,
            )
            if not isinstance(response, (list, tuple)) or len(response) != 3:
                raise ValueError("Invalid trace lease response")
            allowed, active, retry = (int(value) for value in response)
            if allowed not in (0, 1) or not (0 <= active <= self.max_inflight) or retry < 0:
                raise ValueError("Invalid trace lease fields")
            if allowed and (active < 1 or retry != 0):
                raise ValueError("Invalid granted trace lease")
            return (token if allowed else None), max(1, retry) if not allowed else 0
        except (redis_module.RedisError, OSError, TypeError, ValueError, AttributeError) as exc:
            logger.warning(
                "Trace index in-flight acquisition denied: %s", type(exc).__name__
            )
            return None, self.ttl_seconds

    def release(self, token):
        if not isinstance(token, str) or len(token) != 32:
            return False
        if any(ch not in "0123456789abcdef" for ch in token):
            return False
        try:
            removed = _get_redis().eval(
                TRACE_INDEX_LEASE_RELEASE_LUA, 1, self.key, token,
            )
            if type(removed) is not int or removed not in (0, 1):
                raise ValueError("Invalid trace lease release response")
            return bool(removed)
        except (redis_module.RedisError, OSError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("Trace index lease release deferred to TTL: %s", type(exc).__name__)
            return False


TRACE_INDEX_LEASES = TraceIndexLeases()
