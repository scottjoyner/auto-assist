from __future__ import annotations

import os
import time
import logging
import uuid
from typing import Optional

import redis as redis_module

# Phase 2 swarm bootstrap: api.py imports this module before constructing the
# FastAPI app, so this is the least invasive place to attach the swarm router
# and extend Neo4jClient.ensure_schema without replacing the large legacy API.
try:  # pragma: no cover - import guard keeps legacy runtime resilient
    from .swarm_routes import install_swarm_routes_patch

    install_swarm_routes_patch()
except Exception as exc:  # pragma: no cover
    logging.getLogger(__name__).warning("Swarm route bootstrap skipped: %s", exc)

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
