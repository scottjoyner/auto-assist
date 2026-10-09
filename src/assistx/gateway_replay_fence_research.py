"""UNWIRED research-only shared Redis nonce fence for signed gateway assertions.

This store is NOT production authority: a Redis run_id pin is NOT an
independently attested durable generation, and process restarts invalidate
prior replay history. Callers must hold an externally approved generation;
do not generate an untrusted new pin after a restart.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any

_RUN_ID = re.compile(r"^[0-9a-f]{40}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_SUBJECT = re.compile(r"^[A-Za-z0-9._%+\-]{1,128}@[A-Za-z0-9.\-]{1,126}$")
_KEY_ROOT = "assistx:gateway-replay:{receiver-owned}:"
MAX_TTL_MS = 33_000

# All clock and SET NX actions are atomic within a single Redis EVAL.
# No operator identity is ever written in clear to Redis keys or values.
CONSUME_NONCE_LUA = """
local nowparts = redis.call('TIME')
local now = tonumber(nowparts[1]) * 1000 + math.floor(tonumber(nowparts[2]) / 1000)
local expires = tonumber(ARGV[1])
if not expires or expires <= now or expires - now > 33000 then return -1 end
local changed = redis.call('SET', KEYS[1], '1', 'PX', expires - now, 'NX')
if changed then return 1 end
return 0
"""


class GatewayReplayUnavailable(RuntimeError):
    """Deny when receiver-owned replay authority is unavailable or invalid."""


@dataclass(frozen=True)
class GatewayReplayFence:
    redis_client: Any
    receiver_key: str
    approved_redis_run_id: str

    def _pin(self) -> bool:
        if not isinstance(self.approved_redis_run_id, str) or not _RUN_ID.fullmatch(
            self.approved_redis_run_id
        ):
            return False
        try:
            info = self.redis_client.info(section="server")
        except Exception:
            return False
        return (
            isinstance(info, dict)
            and type(info.get("run_id")) is str
            and info["run_id"] == self.approved_redis_run_id
        )

    def consume(self, subject: str, nonce: str, expires_at_ms: int) -> bool:
        """Atomic single-use nonce check; raises/denies on all store faults.

        A False return means a correctly formed, already spent token. A store
        outage or Redis-generation change is an error and never means usable.
        """
        if (
            not isinstance(subject, str) or not _SUBJECT.fullmatch(subject)
            or not isinstance(nonce, str) or not _NONCE.fullmatch(nonce)
            or type(expires_at_ms) is not int or expires_at_ms <= 0
            or not isinstance(self.receiver_key, str)
            or len(self.receiver_key) < 32
            or not self._pin()
        ):
            raise GatewayReplayUnavailable("receiver nonce custody unavailable")
        try:
            digest = hmac.new(
                self.receiver_key.encode("utf-8"),
                ("assistx:gateway:nonce:v1:" + subject + ":" + nonce).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            result = self.redis_client.eval(
                CONSUME_NONCE_LUA, 1, _KEY_ROOT + digest, expires_at_ms
            )
        except Exception as exc:
            raise GatewayReplayUnavailable("receiver nonce store unavailable") from exc
        if type(result) is not int or result not in (0, 1, -1):
            raise GatewayReplayUnavailable("invalid receiver nonce acknowledgment")
        if not self._pin():
            raise GatewayReplayUnavailable("Redis generation changed during nonce claim")
        if result == -1:
            raise GatewayReplayUnavailable("receiver nonce lifetime invalid")
        return result == 1
