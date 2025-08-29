import os, json, time, uuid, redis
from typing import Optional, Dict, Any

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ANSWERS_TTL_S = int(os.getenv("ANSWERS_TTL_S", "86400"))  # 24h default
_r = redis.from_url(REDIS_URL, decode_responses=True)

def _key(answer_id: str) -> str:
    return f"assistx:answers:{answer_id}"

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

def set_result(answer_id: str, data: Dict[str, Any]) -> None:
    obj = get_answer(answer_id)
    if not obj:
        return
    obj["data"] = data
    obj["error"] = None
    obj["status"] = "DONE"
    obj["updated_at"] = int(time.time() * 1000)
    _r.setex(_key(answer_id), ANSWERS_TTL_S, json.dumps(obj))

def set_error(answer_id: str, err: str) -> None:
    obj = get_answer(answer_id)
    if not obj:
        return
    obj["error"] = err
    obj["status"] = "FAILED"
    obj["updated_at"] = int(time.time() * 1000)
    _r.setex(_key(answer_id), ANSWERS_TTL_S, json.dumps(obj))

def get_answer(answer_id: str) -> Optional[Dict[str, Any]]:
    val = _r.get(_key(answer_id))
    if not val:
        return None
    try:
        return json.loads(val)
    except Exception:
        return None
