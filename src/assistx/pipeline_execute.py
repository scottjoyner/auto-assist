
from __future__ import annotations
from .neo4j_client import Neo4jClient
from .agents.orchestrator import run_task
from .logging_utils import get_logger
from .acceptance import evaluate_acceptance

logger = get_logger()

def execute_ready(neo: Neo4jClient, limit: int = 5, dry_run: bool = False):
    tasks = neo.get_ready_tasks(limit=limit)
    for t in tasks:
        neo.update_task_status(t["id"], "RUNNING")
        state = run_task(neo, dict(t), dry_run=dry_run)
        rid = state.get('run_id')
        result = state.get('result')
        acc = evaluate_acceptance(neo, t, rid) if rid else {"passed": bool(result)}
        final_status = "DONE" if (acc.get('passed') or dry_run) else "FAILED"
        if rid:
            neo.log_tool_call(rid, 'acceptance', {'task_id': t['id'], 'acceptance': t.get('acceptance')}, acc, final_status=='DONE')
        neo.update_task_status(t["id"], final_status)
        logger.info(f"Executed {t['id']} -> {final_status}; acceptance: {acc}")
