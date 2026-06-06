
from __future__ import annotations
import os

from .deps import load_queue_class, load_redis_module

Queue = load_queue_class()
Redis = load_redis_module()

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

def get_q() -> Queue:
    r = Redis.from_url(REDIS_URL)
    return Queue("assistx", connection=r)
