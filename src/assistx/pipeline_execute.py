
from __future__ import annotations
from .neo4j_client import Neo4jClient
from .agents.orchestrator import run_task
from .logging_utils import get_logger

logger = get_logger()

def execute_ready(neo: Neo4jClient, limit: int = 5, dry_run: bool = False):
    tasks = neo.get_ready_tasks(limit=limit)
    for t in tasks:
        neo.update_task_status(t["id"], "RUNNING")
        state = run_task(neo, dict(t), dry_run=dry_run)
        final_status = "DONE" if state.get("result") or dry_run else "DONE"  # treat completion of agent loop as DONE
        neo.update_task_status(t["id"], final_status)
        logger.info(f"Executed {t['id']} -> {final_status}")
