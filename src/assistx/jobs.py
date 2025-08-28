
from __future__ import annotations
from .neo4j_client import Neo4jClient
from .agents.orchestrator import run_task
from .acceptance import evaluate_acceptance

def execute_task_job(task_id: str, dry_run: bool = False):
    neo = Neo4jClient()
    with neo.driver.session() as s:
        rec = s.run("MATCH (t:Task{id:$id}) RETURN t", {"id": task_id}).single()
        if not rec:
            neo.close(); return {"error": "task not found", "task_id": task_id}
        t = dict(rec[0])
    neo.update_task_status(task_id, "RUNNING")
    try:
        state = run_task(neo, t, dry_run=dry_run)
        rid = state.get('run_id')
        acc = evaluate_acceptance(neo, t, rid) if rid else {"passed": True}
        final_status = "DONE" if (acc.get('passed') or dry_run) else "FAILED"
        if rid:
            neo.log_tool_call(rid, 'acceptance', {'task_id': task_id, 'acceptance': t.get('acceptance')}, acc, final_status=='DONE')
        neo.update_task_status(task_id, final_status)
        return {"status": final_status, "state": state, "task_id": task_id, "acceptance": acc}
    except Exception as e:
        neo.update_task_status(task_id, "FAILED")
        return {"status": "FAILED", "error": str(e), "task_id": task_id}
    finally:
        neo.close()
