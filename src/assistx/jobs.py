
from __future__ import annotations
from .neo4j_client import Neo4jClient
from .agents.orchestrator import run_task

def execute_task_job(task_id: str, dry_run: bool = False):
    neo = Neo4jClient()
    with neo.driver.session() as s:
        rec = s.run("MATCH (t:Task{id:$id}) RETURN t", {"id": task_id}).single()
        if not rec:
            neo.close()
            return {"error": "task not found", "task_id": task_id}
        t = dict(rec[0])
    neo.update_task_status(task_id, "RUNNING")
    try:
        state = run_task(neo, t, dry_run=dry_run)
        neo.update_task_status(task_id, "DONE")
        return {"status": "DONE", "state": state, "task_id": task_id}
    except Exception as e:
        neo.update_task_status(task_id, "FAILED")
        return {"status": "FAILED", "error": str(e), "task_id": task_id}
    finally:
        neo.close()
