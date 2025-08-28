
from __future__ import annotations
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .neo4j_client import Neo4jClient

app = FastAPI(title="AssistX Review UI")
templates = Jinja2Templates(directory=str(__import__("pathlib").Path(__file__).resolve().parents[2] / "templates"))

app.mount("/static", StaticFiles(directory=str(__import__("pathlib").Path(__file__).resolve().parents[2] / "static")), name="static")

def _neo():
    return Neo4jClient()

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/tasks/review", response_class=HTMLResponse)
def tasks_review(request: Request, limit: int = 50):
    neo = _neo()
    tasks = neo.get_review_tasks(limit=limit)
    neo.close()
    return templates.TemplateResponse("review.html", {"request": request, "tasks": tasks})

@app.post("/tasks/{task_id}/approve")
def approve_task(task_id: str):
    neo = _neo()
    neo.update_task_status(task_id, "READY")
    neo.close()
    return RedirectResponse(url="/tasks/review", status_code=303)

@app.get("/tasks/ready", response_class=HTMLResponse)
def tasks_ready(request: Request, limit: int = 50):
    neo = _neo()
    with neo.driver.session() as s:
        res = s.run("MATCH (t:Task {status:'READY'}) RETURN t ORDER BY t.created_at LIMIT $limit", {"limit": limit})
        tasks = [dict(r[0]) for r in res]
    neo.close()
    return templates.TemplateResponse("ready.html", {"request": request, "tasks": tasks})

@app.get("/runs", response_class=HTMLResponse)
def runs(request: Request, limit: int = 50):
    neo = _neo()
    with neo.driver.session() as s:
        res = s.run("MATCH (r:AgentRun) RETURN r ORDER BY r.started_at DESC LIMIT $limit", {"limit": limit})
        runs = [dict(r[0]) for r in res]
    neo.close()
    return templates.TemplateResponse("runs.html", {"request": request, "runs": runs})
