import os, json, hashlib
from typing import Optional, Dict, Any

from .deps import load_redis_module

redis = load_redis_module()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
IDEMP_TTL_S = int(os.getenv("IDEMP_TTL_S", "3600"))
_r = redis.from_url(REDIS_URL, decode_responses=True)

def _key(k: str) -> str:
    return f"assistx:idemp:{k}"

def principal_key(principal: str, idempotency_key: str) -> str:
    """Domain-separated, principal-scoped key for a client idempotency key.

    The authenticated principal (``auth`` result) must be part of the key:
    otherwise two callers reusing the same client-supplied key collide in the
    shared Redis fast path and one receives the other's cached answer_id or
    sync_result. Neither the principal nor the raw key is stored in clear.
    """
    if not isinstance(principal, str) or not principal.strip():
        raise ValueError("idempotency principal is required")
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256:
        raise ValueError("idempotency key is invalid")
    encoded = json.dumps(
        ["assistx-ask-v1", principal, idempotency_key],
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(b"assistx-idempotency\0" + encoded).hexdigest()

def save(key: str, record: Dict[str, Any]) -> None:
    _r.setex(_key(key), IDEMP_TTL_S, json.dumps(record))

def load(key: str) -> Optional[Dict[str, Any]]:
    val = _r.get(_key(key))
    return json.loads(val) if val else None

def save_for(principal: str, idempotency_key: str, record: Dict[str, Any]) -> None:
    save(principal_key(principal, idempotency_key), record)

def load_for(principal: str, idempotency_key: str) -> Optional[Dict[str, Any]]:
    return load(principal_key(principal, idempotency_key))
