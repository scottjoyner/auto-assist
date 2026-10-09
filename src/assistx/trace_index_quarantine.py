"""Research-only pessimistic trace-index occupancy. NOT ROUTE WIRED.

Atomic Redis hash tokens have *no automatic expiry*: crash/uncertain cancellation
quarantines capacity rather than admitting a successor while a prior Neo4j query
might still be executing. An external loss of Redis state or split brain can
still violate the physical cap, so this is NOT a production safety proof.

Callers must explicitly inject a disposable Redis-like client for tests.
"""
import logging
import uuid

LOGGER = logging.getLogger(__name__)

QUARANTINE_ACQUIRE_LUA = """
-- trace_quarantine_acquire_v1
local key = KEYS[1]
local cap = tonumber(ARGV[1])
local token = ARGV[2]
local capacity = redis.call('HLEN', key)
if capacity >= cap then
    return {0, capacity}
end
if redis.call('HEXISTS', key, token) == 1 then
    return {0, capacity}
end
local tm = redis.call('TIME')
local created_ms = tonumber(tm[1])*1000 + math.floor(tonumber(tm[2])/1000)
redis.call('HSET', key, token, tostring(created_ms))
return {1, capacity+1}
"""

QUARANTINE_RELEASE_LUA = """
-- trace_quarantine_release_v1
return redis.call('HDEL', KEYS[1], ARGV[1])
"""

QUARANTINE_INSPECT_LUA = """
-- trace_quarantine_inspect_v1
return redis.call('HLEN', KEYS[1])
"""


class TraceIndexQuarantine:
    """Synthetic, fail-closed single-Redis admission proof of concept.

    This intentionally retains stale occupancy indefinitely. An operator
    with independent proof of remote query cancellation must establish a
    separate audited recovery process before production admission is enabled.
    Redis FLUSH or failover loss invalidates occupancy and is NOT safe.
    """

    key = "research:{assistx_trace_index}:physical_quarantine"

    def __init__(self, redis_client, slots=3):
        if redis_client is None:
            raise ValueError("EXPLICIT_REDIS_CLIENT_REQUIRED")
        if type(slots) is not int or not 1 <= slots <= 16:
            raise ValueError("INVALID_SLOT_CAP")
        self.client = redis_client
        self.slots = slots

    def acquire(self):
        token = uuid.uuid4().hex
        try:
            raw = self.client.eval(QUARANTINE_ACQUIRE_LUA, 1,
                                   self.key, self.slots, token)
            if (type(raw) not in (tuple, list) or len(raw) != 2 or
                type(raw[0]) is not int or type(raw[1]) is not int or
                raw[0] not in (0, 1) or not 0 <= raw[1] <= self.slots or
                (raw[0] == 1 and raw[1] < 1)):
                raise ValueError("INVALID_REDIS_RESPONSE")
            return (token if raw[0] else None), raw[1]
        except Exception as exc:
            LOGGER.warning("Quarantine admission denied: %s", type(exc).__name__)
            return None, None

    def acknowledge_complete(self, token, *, remote_query_termination_verified=False):
        """Do not release just because a local handler lost its lease or timed out.

        'remote_query_termination_verified' is a research-test assertion,
        NOT an authenticated Neo4j cancellation receipt.
        """
        if remote_query_termination_verified is not True:
            return False
        if (type(token) is not str or len(token) != 32 or
            any(ch not in "0123456789abcdef" for ch in token)):
            return False
        try:
            removed = self.client.eval(QUARANTINE_RELEASE_LUA, 1, self.key, token)
            if type(removed) is not int or removed not in (0, 1):
                raise ValueError("INVALID_RELEASE_RESPONSE")
            return removed == 1
        except Exception as exc:
            LOGGER.warning("Quarantine release uncertain: %s", type(exc).__name__)
            return False

    def inspect_count(self):
        try:
            result = self.client.eval(QUARANTINE_INSPECT_LUA, 1, self.key)
            if type(result) is not int or result < 0 or result > self.slots:
                raise ValueError("INVALID_INSPECTION_RESULT")
            return result
        except Exception:
            return None
