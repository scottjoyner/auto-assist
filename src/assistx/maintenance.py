from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any, Dict

from .deps import load_get_current_job, load_redis_module

redis_module = load_redis_module()
get_current_job = load_get_current_job()

_redis = None
def _get_redis():
    global _redis
    if _redis is None:
        _redis = redis_module.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    return _redis

from .queue import get_q
from . import answers_store

logger = logging.getLogger(__name__)

MAINTENANCE_INTERVAL_SECONDS = int(os.getenv("MAINTENANCE_INTERVAL_SECONDS", "1800"))
TASK_RETENTION_DAYS = int(os.getenv("TASK_RETENTION_DAYS", "30"))
MEMORY_RETENTION_DAYS = int(os.getenv("MEMORY_RETENTION_DAYS", "90"))


def maintenance_job() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "tasks_deleted": 0,
        "memory_deleted": 0,
        "answers_reindexed": 0,
    }
    from .neo4j_client import Neo4jClient
    neo = Neo4jClient()
    try:
        with neo._session() as s:
            rec1 = s.run(
                """
                MATCH (t:Task)
                WHERE t.status IN ['DONE','FAILED','CANCELLED']
                  AND coalesce(t.updated_at_ts, t.completed_at_ts, t.created_at_ts, 0) <
                      timestamp() - ($days * 24 * 60 * 60 * 1000)
                WITH t LIMIT 5000
                DETACH DELETE t
                RETURN count(*) AS deleted
                """,
                {"days": TASK_RETENTION_DAYS},
            ).single()
            result["tasks_deleted"] = int(rec1["deleted"] if rec1 else 0)

            rec2 = s.run(
                """
                MATCH (m:MemoryItem)
                WHERE coalesce(m.updated_at_ts, m.created_at_ts, 0) <
                      timestamp() - ($days * 24 * 60 * 60 * 1000)
                WITH m LIMIT 5000
                DETACH DELETE m
                RETURN count(*) AS deleted
                """,
                {"days": MEMORY_RETENTION_DAYS},
            ).single()
            result["memory_deleted"] = int(rec2["deleted"] if rec2 else 0)

        try:
            idx = answers_store.rebuild_index()
            result["answers_reindexed"] = int(idx.get("reindexed", 0))
        except Exception as e:
            logger.warning("answers index rebuild failed: %s", e)

        logger.info("maintenance_job result=%s", result)
        return result
    finally:
        neo.close()
        _reschedule()


def _reschedule() -> None:
    if get_current_job() is None:
        return
    try:
        get_q().enqueue_in(timedelta(seconds=MAINTENANCE_INTERVAL_SECONDS), maintenance_job)
    except Exception as e:
        logger.error("Failed to reschedule maintenance job: %s", e)


def schedule_maintenance_job() -> None:
    r = _get_redis()
    lock_key = "assistx:maintenance:scheduled"
    if r.setnx(lock_key, "1"):
        r.expire(lock_key, 120)
        get_q().enqueue_in(timedelta(seconds=15), maintenance_job)
        logger.info("Maintenance job scheduled (first run in 15s)")
    else:
        logger.debug("Maintenance job already scheduled")
