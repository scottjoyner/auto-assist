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
STALE_CLAIM_REAP_SECONDS = int(os.getenv("STALE_CLAIM_REAP_SECONDS", "600"))


def maintenance_job() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "tasks_deleted": 0,
        "memory_deleted": 0,
        "answers_reindexed": 0,
        "stale_claims_reaped": 0,
    }
    from .neo4j_client import Neo4jClient
    neo = Neo4jClient()
    try:
        from .legacy_task_maintenance import (
            reap_stale_legacy_tasks,
            retain_terminal_legacy_tasks,
            retain_unlinked_legacy_memory_items,
        )
        # Never let an uncoordinated fallback mutate Task rows. Failure of
        # task maintenance is reported while unrelated memory cleanup can run.
        try:
            result["stale_claims_reaped"] = reap_stale_legacy_tasks(
                neo, lease_seconds=STALE_CLAIM_REAP_SECONDS,
            )
        except Exception as exc:
            logger.warning("bounded Task reap denied: %s", exc)

        try:
            result["tasks_deleted"] = retain_terminal_legacy_tasks(
                neo, retention_days=TASK_RETENTION_DAYS,
            )
        except Exception as exc:
            logger.warning("bounded Task retention denied: %s", exc)

        # Independent MemoryItem deletion could erase linked, protected
        # Task outcome evidence. This experimental path must take the same
        # canonical write lock and explicitly preserve ALL Task-linked memory.
        try:
            result["memory_deleted"] = retain_unlinked_legacy_memory_items(
                neo, retention_days=MEMORY_RETENTION_DAYS,
            )
        except Exception as exc:
            logger.warning("bounded MemoryItem retention denied: %s", exc)

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


def run_stale_claim_reaper_loop() -> None:
    """Start a daemon background thread that periodically resets dead
    CLAIMED/RUNNING tasks back to READY so the fleet can re-acquire them.
    Independent of the RQ worker (which may be idle), so the reaper always
    fires as long as the API process is up."""
    import threading

    def _loop() -> None:
        import time as _time
        interval = int(os.getenv("STALE_CLAIM_REAP_INTERVAL_SECONDS", "300"))
        _time.sleep(30)
        while True:
            try:
                from .neo4j_client import Neo4jClient
                neo = Neo4jClient()
                try:
                    from .legacy_task_maintenance import reap_stale_legacy_tasks
                    reaped = reap_stale_legacy_tasks(
                        neo, lease_seconds=STALE_CLAIM_REAP_SECONDS,
                    )
                    if reaped:
                        logger.info("stale claim reaper reset %d legacy task(s) to READY", reaped)
                finally:
                    neo.close()
            except Exception as e:
                logger.warning("stale claim reaper error: %s", e)
            _time.sleep(interval)

    t = threading.Thread(target=_loop, name="stale-claim-reaper", daemon=True)
    t.start()
    logger.info("stale claim reaper thread started")
