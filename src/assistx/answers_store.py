# src/assistx/answers_store.py
import os, json, time, uuid, redis
from typing import Optional, Dict, Any, List, Iterable

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ANSWERS_TTL_S = int(os.getenv("ANSWERS_TTL_S", "86400"))  # 24h
CHANNEL_PREFIX = "assistx:answers"  # channel base for pub/sub

_r = redis.from_url(REDIS_URL, decode_responses=True)

def _key(answer_id: str) -> str:
    return f"{CHANNEL_PREFIX}:{answer_id}"

def _chan(answer_id: str) -> str:
    return f"{CHANNEL_PREFIX}:{answer_id}:events"

def _publish(answer: Dict[str, Any]) -> None:
    try:
        _r.publish(_chan(answer["id"]), json.dumps({"type": "update", "data": answer}))
    except Exception:
        pass

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
    _publish(obj)

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
    _publish(obj)

def set_result(answer_id: str, data: Dict[str, Any]) -> None:
    obj = get_answer(answer_id)
    if not obj:
        return
    obj["data"] = data
    obj["error"] = None
    obj["status"] = "DONE"
    obj["updated_at"] = int(time.time() * 1000)
    _r.setex(_key(answer_id), ANSWERS_TTL_S, json.dumps(obj))
    _publish(obj)

def set_error(answer_id: str, err: str) -> None:
    obj = get_answer(answer_id)
    if not obj:
        return
    obj["error"] = err
    obj["status"] = "FAILED"
    obj["updated_at"] = int(time.time() * 1000)
    _r.setex(_key(answer_id), ANSWERS_TTL_S, json.dumps(obj))
    _publish(obj)

def get_answer(answer_id: str) -> Optional[Dict[str, Any]]:
    val = _r.get(_key(answer_id))
    if not val:
        return None
    try:
        return json.loads(val)
    except Exception:
        return None

def list_answers(
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """
    Scan Redis, filter, and return most-recent first (updated_at desc).
    For moderate volumes. For huge volumes, we can add sorted-set index later.
    """
    items: List[Dict[str, Any]] = []
    q_norm = (q or "").strip().lower()
    # scan keys
    for k in _r.scan_iter(match=f"{CHANNEL_PREFIX}:*", count=1000):
        if k.endswith(":events"):
            continue
        try:
            obj = json.loads(_r.get(k) or "{}")
        except Exception:
            continue
        if not obj:
            continue
        if status and obj.get("status") != status:
            continue
        if q_norm and q_norm not in (obj.get("question","").lower()):
            continue
        items.append(obj)
    items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return {"items": items[:limit], "count": len(items[:limit])}
