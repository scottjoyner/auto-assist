
from __future__ import annotations
from .neo4j_client import Neo4jClient
from .agents.orchestrator import run_task
from .acceptance import evaluate_acceptance
import os, traceback, time
from typing import Optional
from .neo4j_client import Neo4jClient
from .pipeline.qa_pipeline import answer_question
from .answers_store import set_status, set_result, set_error
from .metrics import JOBS_STARTED, JOBS_SUCCEEDED, JOBS_FAILED
from . import answers_store


def ask_job(answer_id: str, question: str):
    answers_store.set_status(answer_id, "RUNNING")
    # TODO: call your real pipeline here, publish intermediate statuses if you want
    time.sleep(1.0)
    result = {
        "answer": f"Echo: {question}",
        "data_preview": {"note": "This is a stub result; wire your real QA pipeline here."},
        "cypher": "/* generated cypher would go here */",
        "analysis_code": "# python analysis would go here",
    }
    answers_store.set_result(answer_id, result)

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


def ask_question_job(
    answer_id: str,
    question: str,
    model: Optional[str] = None,
    max_repairs: int = 3,
    deliverable_id: Optional[str] = None,
) -> None:
    from .answers_store import set_status, set_result, set_error
    JOBS_STARTED.inc()
    set_status(answer_id, "RUNNING")
    neo = Neo4jClient()
    try:
        out = answer_question(neo, question=question, model=model, max_repairs=max_repairs, log_to_neo=True)
        set_status(answer_id, "RUNNING", run_id=out.get("run_id"))
        if deliverable_id:
            completed = neo.complete_deliverable(
                deliverable_id=deliverable_id,
                answer_id=answer_id,
                status="DONE",
                summary=out.get("answer"),
                result=out,
            )
            out["deliverable_id"] = deliverable_id
            out["deliverable_status"] = completed.get("status") if completed else "UNKNOWN"
        set_result(answer_id, out)
        if deliverable_id:
            answers_store.publish_event(
                answer_id,
                "deliverable_completed",
                {
                    "deliverable_id": deliverable_id,
                    "deliverable_status": "DONE",
                    "summary": out.get("answer"),
                },
            )
        JOBS_SUCCEEDED.inc()
    except Exception as e:
        if deliverable_id:
            try:
                neo.complete_deliverable(
                    deliverable_id=deliverable_id,
                    answer_id=answer_id,
                    status="FAILED",
                    summary=str(e),
                    result={"error": str(e)},
                )
                answers_store.publish_event(
                    answer_id,
                    "deliverable_completed",
                    {
                        "deliverable_id": deliverable_id,
                        "deliverable_status": "FAILED",
                        "summary": str(e),
                    },
                )
            except Exception:
                pass
        import traceback
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        set_error(answer_id, tb)
        JOBS_FAILED.inc()
        raise
    finally:
        neo.close()
