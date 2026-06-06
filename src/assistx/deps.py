from __future__ import annotations

import os
from typing import Any, Tuple


def dependency_mode() -> str:
    value = os.getenv("ASSISTX_DEPENDENCY_MODE", os.getenv("ASSISTX_RUNTIME_PROFILE", "production"))
    return value.strip().lower() or "production"


def use_compat_shims() -> bool:
    return dependency_mode() in {"compat", "test", "testing", "minimal", "dev", "development"}


def load_redis_module():
    if use_compat_shims():
        from .compat import InMemoryRedis as redis_module

        return redis_module
    import redis as redis_module
    return redis_module


def load_aioredis_module():
    if use_compat_shims():
        from .compat import AsyncRedisShim as aioredis

        return aioredis
    import redis.asyncio as aioredis
    return aioredis


def load_queue_class():
    if use_compat_shims():
        from .compat import InMemoryQueue as Queue

        return Queue
    from rq import Queue
    return Queue


def load_get_current_job():
    if use_compat_shims():

        def get_current_job():
            return None

        return get_current_job
    from rq import get_current_job
    return get_current_job


def load_prometheus_client() -> Tuple[str, Any]:
    if use_compat_shims():
        return "text/plain; charset=utf-8", lambda: b""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return CONTENT_TYPE_LATEST, generate_latest


def multipart_available() -> bool:
    try:
        import multipart  # noqa: F401
        return True
    except ModuleNotFoundError:
        return False
