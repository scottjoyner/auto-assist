
from __future__ import annotations
from .neo4j_client import Neo4jClient
from .agents.orchestrator import run_task
from .acceptance import evaluate_acceptance
import os, traceback
from typing import Optional
from .neo4j_client import Neo4jClient
from .pipeline.qa_pipeline import answer_question
from .answers_store import set_status, set_result, set_error
from .metrics import JOBS_STARTED, JOBS_SUCCEEDED, JOBS_FAILED

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


def ask_question_job(answer_id: str, question: str, model: Optional[str] = None, max_repairs: int = 3) -> None:
    from .answers_store import set_status, set_result, set_error
    JOBS_STARTED.inc()
    set_status(answer_id, "RUNNING")
    neo = Neo4jClient()
    try:
        out = answer_question(neo, question=question, model=model, max_repairs=max_repairs, log_to_neo=True)
        set_status(answer_id, "RUNNING", run_id=out.get("run_id"))
        set_result(answer_id, out)
        JOBS_SUCCEEDED.inc()
    except Exception as e:
        import traceback
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        set_error(answer_id, tb)
        JOBS_FAILED.inc()
        raise
    finally:
        neo.close()