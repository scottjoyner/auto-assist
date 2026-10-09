"""Research-only, NOT WIRED: single atomic trace-index rate + in-flight admission.

No imports or runtime hooks initiate Redis I/O. This module must not be
connected to a deployed route until proxy identity and concurrency admission
receive an independent staging security review. No provider-budget authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import secrets
from typing import Any


class AdmissionUnavailable(RuntimeError):
    """Fail-closed invalid configuration, custody or quota-store response."""


# Four keys live in the *same* Redis Cluster hash slot. The per-identity hash
# uses an independently provisioned HMAC key; raw usernames are not keys.
# Redis TIME owns the clock; denials do not consume rate or in-flight slots.
ACQUIRE_LUA = """
local per_rate, all_rate, per_active, all_active = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local per_max, all_max = tonumber(ARGV[1]), tonumber(ARGV[2])
local window, per_slots, all_slots = tonumber(ARGV[3]), tonumber(ARGV[4]), tonumber(ARGV[5])
local lease_ms, nonce = tonumber(ARGV[6]), ARGV[7]
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local rate_cutoff = now - window
redis.call('ZREMRANGEBYSCORE', per_rate, '-inf', rate_cutoff)
redis.call('ZREMRANGEBYSCORE', all_rate, '-inf', rate_cutoff)
redis.call('ZREMRANGEBYSCORE', per_active, '-inf', now)
redis.call('ZREMRANGEBYSCORE', all_active, '-inf', now)
if redis.call('ZSCORE', all_active, nonce) or redis.call('ZSCORE', per_active, nonce) then
  return {0, 5, 1}
end
local pr = redis.call('ZCARD', per_rate)
local gr = redis.call('ZCARD', all_rate)
local pa = redis.call('ZCARD', per_active)
local ga = redis.call('ZCARD', all_active)
if pr >= per_max then
  local oldest = redis.call('ZRANGE', per_rate, 0, 0, 'WITHSCORES')
  return {0, 1, math.max(1, math.ceil((window - (now - tonumber(oldest[2]))) / 1000))}
end
if gr >= all_max then
  local oldest = redis.call('ZRANGE', all_rate, 0, 0, 'WITHSCORES')
  return {0, 2, math.max(1, math.ceil((window - (now - tonumber(oldest[2]))) / 1000))}
end
if pa >= per_slots then
  local oldest = redis.call('ZRANGE', per_active, 0, 0, 'WITHSCORES')
  return {0, 3, math.max(1, math.ceil((tonumber(oldest[2]) - now) / 1000))}
end
if ga >= all_slots then
  local oldest = redis.call('ZRANGE', all_active, 0, 0, 'WITHSCORES')
  return {0, 4, math.max(1, math.ceil((tonumber(oldest[2]) - now) / 1000))}
end
redis.call('ZADD', per_rate, now, nonce)
redis.call('ZADD', all_rate, now, nonce)
redis.call('ZADD', per_active, now + lease_ms, nonce)
redis.call('ZADD', all_active, now + lease_ms, nonce)
redis.call('PEXPIRE', per_rate, window * 2)
redis.call('PEXPIRE', all_rate, window * 2)
redis.call('PEXPIRE', per_active, lease_ms * 2)
redis.call('PEXPIRE', all_active, lease_ms * 2)
return {1, 0, 0}
"""

# Only a currently valid exact nonce in BOTH ZSETs can free slots. A stale
# release cannot remove a later request from the same operator.
RELEASE_LUA = """
local per_active, all_active = KEYS[1], KEYS[2]
local nonce = ARGV[1]
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local ps = redis.call('ZSCORE', per_active, nonce)
local gs = redis.call('ZSCORE', all_active, nonce)
if not ps or not gs or tonumber(ps) <= now or tonumber(gs) <= now then return 0 end
redis.call('ZREM', per_active, nonce)
redis.call('ZREM', all_active, nonce)
return 1
"""

# Renewal needs the current nonce in BOTH keys and refuses expired leases.
# Never take an expired request and silently turn it into fresh authority.
RENEW_LUA = """
local per_active, all_active = KEYS[1], KEYS[2]
local nonce, lease_ms = ARGV[1], tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local ps = redis.call('ZSCORE', per_active, nonce)
local gs = redis.call('ZSCORE', all_active, nonce)
if not ps or not gs or tonumber(ps) <= now or tonumber(gs) <= now then return 0 end
redis.call('ZADD', per_active, now + lease_ms, nonce)
redis.call('ZADD', all_active, now + lease_ms, nonce)
redis.call('PEXPIRE', per_active, lease_ms * 2)
redis.call('PEXPIRE', all_active, lease_ms * 2)
return 1
"""

REASONS = {0: "admitted", 1: "principal_rate", 2: "fleet_rate",
           3: "principal_inflight", 4: "fleet_inflight", 5: "nonce_replay"}


@dataclass(frozen=True)
class Policy:
    principal_rate: int = 12
    fleet_rate: int = 60
    window_seconds: int = 60
    principal_inflight: int = 2
    fleet_inflight: int = 3
    lease_seconds: int = 15


@dataclass(frozen=True)
class Lease:
    nonce: str
    per_active: str
    fleet_active: str
    lease_seconds: int


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    retry_after_seconds: int
    lease: Lease | None = None


def _keys(principal: str, secret: str) -> tuple[str, str, str, str]:
    if not isinstance(principal, str) or not principal.strip() or len(principal) > 256:
        raise AdmissionUnavailable("no validated operator identity")
    # Normalize only leading/trailing whitespace, preserving case-dependent
    # account identities. Unknown/untrusted principal sources are never accepted.
    principal = principal.strip()
    if not isinstance(secret, str) or len(secret) < 32:
        raise AdmissionUnavailable("missing receiver-owned quota identity key")
    try:
        digest = hmac.new(secret.encode("utf8"),
                          ("assistx:trace-index:v2:" + principal).encode("utf8"),
                          hashlib.sha256).hexdigest()
    except UnicodeError as exc:
        raise AdmissionUnavailable("invalid identity encoding") from exc
    root = "traceidx:{assistx-trace-index-v2}:"
    return (root + "rate:principal:" + digest,
            root + "rate:fleet",
            root + "active:principal:" + digest,
            root + "active:fleet")


def _policy(policy: Policy) -> None:
    if not isinstance(policy, Policy):
        raise AdmissionUnavailable("invalid policy type")
    for key in ("principal_rate", "fleet_rate", "window_seconds", "principal_inflight",
                "fleet_inflight", "lease_seconds"):
        value = getattr(policy, key)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 86400:
            raise AdmissionUnavailable("invalid policy " + key)
    if policy.principal_rate > policy.fleet_rate or policy.principal_inflight > policy.fleet_inflight:
        raise AdmissionUnavailable("principal allowance exceeds fleet allowance")


def _int_reply(raw: Any, length: int) -> tuple[int, ...]:
    if not isinstance(raw, (list, tuple)) or len(raw) != length:
        raise AdmissionUnavailable("malformed quota reply")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in raw):
        raise AdmissionUnavailable("noninteger quota reply")
    return tuple(raw)


def acquire(redis_client: Any, principal: str, secret: str,
            policy: Policy = Policy(), *, nonce: str | None = None) -> Decision:
    """Single EVAL rate+concurrency admission; Redis error always denies.

    Caller must already have independently authenticated/authorized principal.
    This module is intentionally NOT wired into FastAPI or the older budgets.
    """
    _policy(policy)
    keys = _keys(principal, secret)
    nonce = secrets.token_hex(16) if nonce is None else nonce
    if not isinstance(nonce, str) or len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
        raise AdmissionUnavailable("invalid lease nonce")
    try:
        raw = redis_client.eval(ACQUIRE_LUA, 4, *keys,
                                policy.principal_rate, policy.fleet_rate,
                                policy.window_seconds * 1000, policy.principal_inflight,
                                policy.fleet_inflight, policy.lease_seconds * 1000, nonce)
        granted, reason, retry = _int_reply(raw, 3)
        if granted not in (0, 1) or reason not in REASONS or not 0 <= retry <= 86400:
            raise AdmissionUnavailable("out-of-range quota decision")
        if granted == 1:
            if reason != 0 or retry != 0:
                raise AdmissionUnavailable("inconsistent grant")
            return Decision(True, "admitted", 0,
                            Lease(nonce, keys[2], keys[3], policy.lease_seconds))
        if reason == 0 or retry < 1:
            raise AdmissionUnavailable("inconsistent denial")
        return Decision(False, REASONS[reason], retry)
    except AdmissionUnavailable:
        raise
    except Exception as exc:
        raise AdmissionUnavailable("atomic read admission store unavailable") from exc


def _validate_lease(lease: Lease) -> None:
    if not isinstance(lease, Lease) or not isinstance(lease.nonce, str) or \
            len(lease.nonce) != 32 or \
            any(c not in "0123456789abcdef" for c in lease.nonce):
        raise AdmissionUnavailable("invalid owned lease")
    prefix = "traceidx:{assistx-trace-index-v2}:active:"
    if not isinstance(lease.per_active, str) or \
            not lease.per_active.startswith(prefix + "principal:") or \
            lease.fleet_active != prefix + "fleet":
        raise AdmissionUnavailable("lease identity not in approved namespace")
    if not isinstance(lease.lease_seconds, int) or isinstance(lease.lease_seconds, bool) or \
            not 1 <= lease.lease_seconds <= 86400:
        raise AdmissionUnavailable("lease lifetime not validated")


def release(redis_client: Any, lease: Lease) -> bool:
    _validate_lease(lease)
    try:
        response = redis_client.eval(RELEASE_LUA, 2, lease.per_active, lease.fleet_active,
                                     lease.nonce)
        if type(response) is not int or response not in (0, 1):
            raise AdmissionUnavailable("invalid release acknowledgment")
        return bool(response)
    except AdmissionUnavailable:
        raise
    except Exception as exc:
        raise AdmissionUnavailable("lease release unconfirmed") from exc


def renew(redis_client: Any, lease: Lease) -> bool:
    _validate_lease(lease)
    try:
        response = redis_client.eval(RENEW_LUA, 2, lease.per_active, lease.fleet_active,
                                     lease.nonce, lease.lease_seconds * 1000)
        if type(response) is not int or response not in (0, 1):
            raise AdmissionUnavailable("invalid renewal acknowledgment")
        return bool(response)
    except AdmissionUnavailable:
        raise
    except Exception as exc:
        raise AdmissionUnavailable("lease renewal unconfirmed") from exc
