
from __future__ import annotations
import os
from rq import Queue
from redis import Redis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

def get_q() -> Queue:
    r = Redis.from_url(REDIS_URL)
    return Queue("assistx", connection=r)
