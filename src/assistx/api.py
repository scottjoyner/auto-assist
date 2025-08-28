
from __future__ import annotations
import os
from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from .neo4j_client import Neo4jClient
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from .metrics import REQUESTS
from .pipeline_execute import execute_ready
from .agents.orchestrator import run_task
from .queue import get_q
from .jobs import execute_task_job
from .metrics import EXECUTIONS, REQUESTS

security = HTTPBasic()
USER = os.getenv("BASIC_AUTH_USER", "admin")
PASS = os.getenv("BASIC_AUTH_PASS", "admin")

app = FastAPI(title="AssistX Review UI")
templates = Jinja2Templates(directory=str(__import__("pathlib").Path(__file__).resolve().parents[2] / "templates"))
app.mount("/static", StaticFiles(directory=str(__import__("pathlib").Path(__file__).resolve().parents[2] / "static")), name="static")

def auth(credentials: HTTPBasicCredentials = Depends(security)):
    correct = (credentials.username == USER) and (credentials.password == PASS)
    if not correct:
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return credentials.username

def _neo():
    return Neo4jClient()

@app.get("/", response_class=HTMLResponse)
def home(request: Request, user: str = Depends(auth)):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/tasks/review", response_class=HTMLResponse)
def tasks_review(request: Request, limit: int = 50, user: str = Depends(auth)):
    neo = _neo()
    tasks = neo.get_review_tasks(limit=limit)
    neo.close()
    return templates.TemplateResponse("review.html", {"request": request, "tasks": tasks})

@app.post("/tasks/{task_id}/approve")
def approve_task(task_id: str, user: str = Depends(auth)):
    neo = _neo()
    neo.update_task_status(task_id, "READY")
    neo.close()
    return RedirectResponse(url="/tasks/review", status_code=303)

@app.get("/tasks/ready", response_class=HTMLResponse)
def tasks_ready(request: Request, limit: int = 50, user: str = Depends(auth)):
    neo = _neo()
    with neo.driver.session() as s:
        res = s.run("MATCH (t:Task {status:'READY'}) RETURN t ORDER BY t.created_at LIMIT $limit", {"limit": limit})
        tasks = [dict(r[0]) for r in res]
    neo.close()
    return templates.TemplateResponse("ready.html", {"request": request, "tasks": tasks})

@app.post("/tasks/{task_id}/execute")
def execute_task(task_id: str, dry_run: bool = False, user: str = Depends(auth)):
    neo = _neo()
    with neo.driver.session() as s:
        rec = s.run("MATCH (t:Task{id:$id}) RETURN t", {"id": task_id}).single()
        if not rec:
            neo.close()
            raise HTTPException(status_code=404, detail="Task not found")
        t = dict(rec[0])
    # mark running then execute
    neo.update_task_status(task_id, "RUNNING")
    try:
        result = run_task(neo, t, dry_run=dry_run)
        neo.update_task_status(task_id, "DONE")
        return JSONResponse({"status": "DONE", "task_id": task_id, "state": result})
    except Exception as e:
        neo.update_task_status(task_id, "FAILED")
        raise
    finally:
        neo.close()

@app.get("/runs", response_class=HTMLResponse)
def runs(request: Request, limit: int = 50, user: str = Depends(auth)):
    neo = _neo()
    with neo.driver.session() as s:
        res = s.run("MATCH (r:AgentRun) RETURN r ORDER BY r.started_at DESC LIMIT $limit", {"limit": limit})
        runs = [dict(r[0]) for r in res]
    neo.close()
    return templates.TemplateResponse("runs.html", {"request": request, "runs": runs})


@app.get("/metrics")
def metrics(user: str = Depends(auth)):
    # protect or expose publicly by removing auth dependency
    data = generate_latest()
    return PlainTextResponse(data.decode("utf-8"), media_type=CONTENT_TYPE_LATEST)


@app.post("/tasks/{task_id}/enqueue")
def enqueue_task(task_id: str, dry_run: bool = False, user: str = Depends(auth)):
    q = get_q()
    job = q.enqueue(execute_task_job, task_id, dry_run)
    EXECUTIONS.labels(status="ENQUEUED").inc()
    return {"enqueued": True, "job_id": job.get_id(), "task_id": task_id}
