# src/assistx/answers_store.py
import os, json, time, uuid, redis
from typing import Optional, Dict, Any, List, Tuple



REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")




ANSWERS_TTL_S = int(os.getenv("ANSWERS_TTL_S", "86400"))  # 24h

CHANNEL_PREFIX = "assistx:answers"  # base keyspace
GLOBAL_CHAN = f"{CHANNEL_PREFIX}:events"  # <--- add this
INDEX_ALL = f"{CHANNEL_PREFIX}:index:updated_at"                    # ZSET score=updated_at
INDEX_STATUS_PREFIX = f"{CHANNEL_PREFIX}:index:status:"             # ZSET per status
ALL_STATUSES = ("QUEUED","RUNNING","DONE","FAILED")

_r = redis.from_url(REDIS_URL, decode_responses=True)

# -------- Keys / indexes --------
def _key(answer_id: str) -> str:
    return f"{CHANNEL_PREFIX}:{answer_id}"

# keep _chan(answer_id) for per-answer streams
def _chan(answer_id: str) -> str:
    return f"{CHANNEL_PREFIX}:{answer_id}:events"

# helper (export if you like)
def _global_chan() -> str:
    return GLOBAL_CHAN

# update publisher to emit to both per-answer and global channels
def _publish(answer: Dict[str, Any], ev_type: str = "update") -> None:
    payload = {"type": ev_type, "data": answer}
    try:
        _r.publish(_chan(answer["id"]), json.dumps(payload))
        _r.publish(GLOBAL_CHAN, json.dumps(payload))  # <--- global broadcast
    except Exception:
        pass

# -------- Pub/Sub --------
def _publish(answer: Dict[str, Any]) -> None:
    try:
        _r.publish(_chan(answer["id"]), json.dumps({"type": "update", "data": answer}))
    except Exception:
        pass

# -------- Index helpers --------
def _index_key_for_status(st: Optional[str]) -> str:
    return INDEX_ALL if not st else f"{INDEX_STATUS_PREFIX}{st}"

def _index_upsert(answer: Dict[str, Any]) -> None:
    """Upsert this answer id into global and status zsets with updated_at score."""
    aid = answer["id"]
    score = float(answer.get("updated_at", int(time.time() * 1000)))
    pipe = _r.pipeline(transaction=True)
    # global
    pipe.zadd(INDEX_ALL, {aid: score})
    # move between status indexes
    for st in ALL_STATUSES:
        pipe.zrem(f"{INDEX_STATUS_PREFIX}{st}", aid)
    st_now = answer.get("status")
    if st_now in ALL_STATUSES:
        pipe.zadd(f"{INDEX_STATUS_PREFIX}{st_now}", {aid: score})
    pipe.execute()

def _index_remove(answer_id: str) -> None:
    pipe = _r.pipeline(transaction=True)
    pipe.zrem(INDEX_ALL, answer_id)
    for st in ALL_STATUSES:
        pipe.zrem(f"{INDEX_STATUS_PREFIX}{st}", answer_id)
    pipe.execute()

# -------- CRUD --------
def new_answer_id() -> str:
    return uuid.uuid4().hex

def init_answer(answer_id: str, question: str, user_meta: Optional[Dict[str, Any]] = None) -> None:
    now = int(time.time() * 1000)
    obj = {
        "id": answer_id,
        "question": question,
        "status": "QUEUED",     # QUEUED | RUNNING | DONE | FAILED
        "created_at": now,
        "updated_at": now,
        "job_id": None,
        "run_id": None,
        "data": None,
        "error": None,
        "meta": user_meta or {},
    }
    _r.setex(_key(answer_id), ANSWERS_TTL_S, json.dumps(obj))
    _index_upsert(obj)
    _publish(obj, ev_type="new")

def set_status(answer_id: str, status: str, *, job_id: str = None, run_id: str = None) -> None:
    obj = get_answer(answer_id)
    if not obj:
        return
    obj["status"] = status
    if job_id is not None:
        obj["job_id"] = job_id
    if run_id is not None:
        obj["run_id"] = run_id
    obj["updated_at"] = int(time.time() * 1000)
    _r.setex(_key(answer_id), ANSWERS_TTL_S, json.dumps(obj))
    _index_upsert(obj)
    _publish(obj, ev_type="update")

def set_result(answer_id: str, data: Dict[str, Any]) -> None:
    obj = get_answer(answer_id)
    if not obj:
        return
    obj["data"] = data
    obj["error"] = None
    obj["status"] = "DONE"
    obj["updated_at"] = int(time.time() * 1000)
    _r.setex(_key(answer_id), ANSWERS_TTL_S, json.dumps(obj))
    _index_upsert(obj)
    _publish(obj, ev_type="update")

def set_error(answer_id: str, err: str) -> None:
    obj = get_answer(answer_id)
    if not obj:
        return
    obj["error"] = err
    obj["status"] = "FAILED"
    obj["updated_at"] = int(time.time() * 1000)
    _r.setex(_key(answer_id), ANSWERS_TTL_S, json.dumps(obj))
    _index_upsert(obj)
    _publish(obj, ev_type="update")

def get_answer(answer_id: str) -> Optional[Dict[str, Any]]:
    val = _r.get(_key(answer_id))
    if not val:
        return None
    try:
        return json.loads(val)
    except Exception:
        return None

# -------- Pagination (cursor) --------
def _parse_cursor(cur: Optional[str]) -> Optional[Tuple[float, str]]:
    """Cursor format: '<score>:<id>' where score=updated_at ms."""
    if not cur:
        return None
    try:
        score_str, aid = cur.split(":", 1)
        return float(score_str), aid
    except Exception:
        return None

def list_answers_paginated(
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns newest-first page with optional status filter and substring query:
      { items:[...], count:int, next_cursor:str|None }
    Efficient via ZREVRANGEBYSCORE on global or status index.
    """
    zkey = _index_key_for_status(status if status in ALL_STATUSES else None)
    q_norm = (q or "").strip().lower()
    # max bound (exclusive if cursor present)
    cur = _parse_cursor(cursor)
    maxb = "+inf" if not cur else f"({int(cur[0])}"

    items: List[Dict[str, Any]] = []
    next_cursor = None

    # pull in small windows until we collect 'limit' or exhaust (bounds-only pagination)
    window = max(50, limit * 3)
    loops = 0
    while len(items) < limit and loops < 6:
        batch = _r.zrevrangebyscore(zkey, maxb, "-inf", start=0, num=window, withscores=True)
        if not batch:
            break
        # advance max bound to just below last score in batch
        last_score = int(batch[-1][1])
        maxb = f"({last_score}"
        for aid, score in batch:
            obj = get_answer(aid)
            if not obj:
                # lazy cleanup of stale index entry
                _index_remove(aid)
                continue
            if status and obj.get("status") != status:
                continue  # should not happen, but safe
            if q_norm and q_norm not in (obj.get("question","").lower()):
                continue
            items.append(obj)
            if len(items) == limit:
                next_cursor = f"{obj.get('updated_at', int(score))}:{obj['id']}"
                break
        loops += 1

    return {"items": items, "count": len(items), "next_cursor": next_cursor}

# -------- Admin: rebuild index from existing keys --------
def rebuild_index() -> Dict[str, Any]:
    """One-shot backfill: scan all answer keys, rebuild ZSETs."""
    # wipe indexes
    pipe = _r.pipeline(transaction=True)
    pipe.delete(INDEX_ALL)
    for st in ALL_STATUSES:
        pipe.delete(f"{INDEX_STATUS_PREFIX}{st}")
    pipe.execute()

    total = 0
    for k in _r.scan_iter(match=f"{CHANNEL_PREFIX}:*", count=1000):
        if k.endswith(":events"):
            continue
        try:
            obj = json.loads(_r.get(k) or "{}")
        except Exception:
            continue
        if not obj or "id" not in obj:
            continue
        _index_upsert(obj)
        total += 1
    return {"reindexed": total}
