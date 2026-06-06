from __future__ import annotations

import logging
import os
from datetime import timedelta

from .deps import load_get_current_job, load_redis_module

redis_module = load_redis_module()
get_current_job = load_get_current_job()

from .neo4j_client import Neo4jClient
from .queue import get_q
from .swarm_core import list_model_endpoints, probe_model_endpoint

logger = logging.getLogger(__name__)

PROBE_INTERVAL_SECONDS = int(os.getenv("MODEL_PROBE_INTERVAL", "300"))


def probe_job() -> dict:
    neo = Neo4jClient()
    try:
        endpoints = list_model_endpoints(neo)
        results = []
        for ep in endpoints:
            if ep.get("model_endpoint_id", "").startswith("test-"):
                continue
            result = probe_model_endpoint(neo, ep)
            results.append(result)
        online = sum(1 for r in results if r.get("status") == "online")
        offline = sum(1 for r in results if r.get("status") == "offline")
        logger.info("model_probe_complete total=%d online=%d offline=%d", len(results), online, offline)
        _reschedule()
        return {"probed": len(results), "online": online, "offline": offline, "results": results}
    finally:
        neo.close()


def _reschedule() -> None:
    job = get_current_job()
    if job is not None:
        try:
            get_q().enqueue_in(timedelta(seconds=PROBE_INTERVAL_SECONDS), probe_job)
        except Exception as e:
            logger.error("Failed to reschedule model prober: %s", e)


def schedule_prober() -> None:
    r = redis_module.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    lock_key = "assistx:model_prober:scheduled"
    if r.setnx(lock_key, "1"):
        r.expire(lock_key, 60)
        get_q().enqueue_in(timedelta(seconds=10), probe_job)
        logger.info("Model endpoint prober scheduled (first run in 10s)")
    else:
        logger.debug("Model endpoint prober already scheduled")
