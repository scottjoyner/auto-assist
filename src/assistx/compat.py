from __future__ import annotations

import asyncio
import fnmatch
import json
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple


class RedisError(Exception):
    pass


class InMemoryPubSub:
    def __init__(self, redis: "InMemoryRedis"):
        self._redis = redis
        self._channels: set[str] = set()
        self._queue: Deque[dict[str, Any]] = deque()

    async def subscribe(self, *channels: str) -> None:
        for channel in channels:
            self._channels.add(channel)
            self._redis._pubsubs[channel].add(self)

    async def unsubscribe(self, *channels: str) -> None:
        for channel in channels:
            self._channels.discard(channel)
            self._redis._pubsubs[channel].discard(self)

    def _push(self, channel: str, data: str) -> None:
        self._queue.append({"type": "message", "channel": channel, "data": data})

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0):
        if self._queue:
            return self._queue.popleft()
        await asyncio.sleep(min(timeout, 0.01))
        if self._queue:
            return self._queue.popleft()
        return None

    async def close(self) -> None:
        await self.unsubscribe(*list(self._channels))


class InMemoryPipeline:
    def __init__(self, redis: "InMemoryRedis"):
        self._redis = redis
        self._ops: List[Tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def zremrangebyscore(self, *args, **kwargs):
        self._ops.append(("zremrangebyscore", args, kwargs))
        return self

    def zcard(self, *args, **kwargs):
        self._ops.append(("zcard", args, kwargs))
        return self

    def zadd(self, *args, **kwargs):
        self._ops.append(("zadd", args, kwargs))
        return self

    def expire(self, *args, **kwargs):
        self._ops.append(("expire", args, kwargs))
        return self

    def zrem(self, *args, **kwargs):
        self._ops.append(("zrem", args, kwargs))
        return self

    def delete(self, *args, **kwargs):
        self._ops.append(("delete", args, kwargs))
        return self

    def execute(self):
        results = []
        for op, args, kwargs in self._ops:
            if op == "zremrangebyscore":
                results.append(self._redis.zremrangebyscore(*args, **kwargs))
            elif op == "zcard":
                results.append(self._redis.zcard(*args, **kwargs))
            elif op == "zadd":
                results.append(self._redis.zadd(*args, **kwargs))
            elif op == "expire":
                results.append(self._redis.expire(*args, **kwargs))
            elif op == "zrem":
                results.append(self._redis.zrem(*args, **kwargs))
            elif op == "delete":
                results.append(self._redis.delete(*args, **kwargs))
        self._ops.clear()
        return results


class InMemoryRedis:
    _instances: Dict[str, "InMemoryRedis"] = {}
    RedisError = RedisError

    def __init__(self, url: str = "memory://redis", decode_responses: bool = True):
        self.url = url
        self.decode_responses = decode_responses
        self._kv: Dict[str, str] = {}
        self._zsets: Dict[str, Dict[str, float]] = defaultdict(dict)
        self._pubsubs: Dict[str, set[InMemoryPubSub]] = defaultdict(set)

    @classmethod
    def from_url(cls, url: str, decode_responses: bool = True):
        inst = cls._instances.get(url)
        if inst is None:
            inst = cls(url, decode_responses=decode_responses)
            cls._instances[url] = inst
        return inst

    Redis = None

    def pipeline(self, transaction: bool = True):
        return InMemoryPipeline(self)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._kv[key] = value

    def get(self, key: str) -> Optional[str]:
        return self._kv.get(key)

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += int(key in self._kv)
            self._kv.pop(key, None)
            removed += int(key in self._zsets)
            self._zsets.pop(key, None)
        return removed

    def scan_iter(self, match: str, count: int = 1000):
        for key in list(self._kv.keys()) + list(self._zsets.keys()):
            if fnmatch.fnmatch(key, match):
                yield key

    def publish(self, channel: str, message: str) -> int:
        subscribers = list(self._pubsubs.get(channel, set()))
        for sub in subscribers:
            sub._push(channel, message)
        return len(subscribers)

    def pubsub(self):
        return InMemoryPubSub(self)

    def close(self) -> None:
        return None

    def ping(self) -> bool:
        return True

    def aclose(self) -> None:
        return None

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        z = self._zsets.setdefault(key, {})
        to_delete = [member for member, score in z.items() if score >= float(min_score) and score <= float(max_score)]
        for member in to_delete:
            del z[member]
        return len(to_delete)

    def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))

    def zadd(self, key: str, mapping: Dict[str, float]) -> int:
        z = self._zsets.setdefault(key, {})
        z.update(mapping)
        return len(mapping)

    def expire(self, key: str, ttl: int) -> bool:
        return True

    def zrem(self, key: str, member: str) -> int:
        z = self._zsets.get(key, {})
        return int(z.pop(member, None) is not None)

    def zrange(self, key: str, start: int, stop: int, withscores: bool = False):
        items = sorted(self._zsets.get(key, {}).items(), key=lambda kv: (kv[1], kv[0]))
        subset = items[start : stop + 1 if stop != -1 else None]
        return subset if withscores else [member for member, _ in subset]

    def zrevrangebyscore(self, key: str, max_score, min_score, start: int = 0, num: Optional[int] = None, withscores: bool = False):
        def _bound(v):
            if v == "+inf":
                return float("inf")
            if v == "-inf":
                return float("-inf")
            if isinstance(v, str) and v.startswith("("):
                return float(v[1:]) - 0.000001
            return float(v)

        hi = _bound(max_score)
        lo = _bound(min_score)
        items = [(member, score) for member, score in self._zsets.get(key, {}).items() if lo <= score <= hi]
        items.sort(key=lambda kv: (kv[1], kv[0]), reverse=True)
        if num is not None:
            items = items[start : start + num]
        else:
            items = items[start:]
        return items if withscores else [member for member, _ in items]


class InMemoryJob:
    def __init__(self, job_id: Optional[str] = None):
        self._id = job_id or uuid.uuid4().hex

    def get_id(self):
        return self._id


class InMemoryQueue:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def enqueue(self, func, *args, **kwargs):
        return InMemoryJob()


class AsyncRedisShim:
    def __init__(self, backing: InMemoryRedis):
        self._backing = backing

    @classmethod
    def from_url(cls, url: str, decode_responses: bool = True):
        return cls(InMemoryRedis.from_url(url, decode_responses=decode_responses))

    def pubsub(self):
        return self._backing.pubsub()

    async def aclose(self) -> None:
        return None
