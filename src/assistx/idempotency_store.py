import os, json, time, redis
from typing import Optional, Dict, Any

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
IDEMP_TTL_S = int(os.getenv("IDEMP_TTL_S", "3600"))
_r = redis.from_url(REDIS_URL, decode_responses=True)

def _key(k: str) -> str:
    return f"assistx:idemp:{k}"

def save(key: str, record: Dict[str, Any]) -> None:
    _r.setex(_key(key), IDEMP_TTL_S, json.dumps(record))

def load(key: str) -> Optional[Dict[str, Any]]:
    val = _r.get(_key(key))
    return json.loads(val) if val else None
