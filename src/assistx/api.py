from __future__ import annotations

import os
import uuid
import json
import pathlib
import shutil
from typing import Optional, Dict, Any, List
from fastapi.responses import HTMLResponse
from fastapi import FastAPI, Request, HTTPException, Depends, UploadFile, Form, Header, Query, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from .neo4j_client import Neo4jClient  # unified client
from .agents.orchestrator import *
from .agents.pipeline import *
from .queue import *
from .jobs import *
from .metrics import EXECUTIONS
from pydantic import BaseModel
from .answers_store import *


class AskAsyncIn(BaseModel):
    question: str
    model: str | None = None
    max_repairs: int = 3
    # optional: arbitrary metadata to store alongside the answer (e.g., user/session)
    meta: dict | None = None

class AskIn(BaseModel):
    question: str
    model: str | None = None
    max_repairs: int = 3


# -----------------------
# Config / Security
# -----------------------
security = HTTPBasic()
USER = os.getenv("BASIC_AUTH_USER", "admin")
PASS = os.getenv("BASIC_AUTH_PASS", "admin")

API_TOKEN: Optional[str] = os.getenv("API_TOKEN")  # If set, required for /upload-audio

TRANSCRIPTIONS_ROOT = pathlib.Path(os.getenv("TRANSCRIPTIONS_ROOT", "./transcriptions")).resolve()
TRANSCRIPTIONS_ROOT.mkdir(parents=True, exist_ok=True)

WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")        # e.g., "cuda", "cpu", "auto"
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE_TYPE", "int8") # e.g., "float16", "int8"

# -----------------------
# App + Static/Template
# -----------------------
app = FastAPI(title="AssistX API & UI")

# CORS is useful for the ingestion endpoints (web UIs, local tools, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static & templates like v1
ROOT = pathlib.Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(ROOT / "templates"))
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


def auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not (credentials.username == USER and credentials.password == PASS):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


def _neo() -> Neo4jClient:
    neo = Neo4jClient()
    return neo


# -----------------------
# Whisper model cache
# -----------------------
# Lazy-load and reuse models by name to avoid reinitializing on every request
_WHISPER_CACHE: Dict[str, Any] = {}

def get_whisper_model(model_name: str):
    from faster_whisper import WhisperModel
    if model_name not in _WHISPER_CACHE:
        _WHISPER_CACHE[model_name] = WhisperModel(
            model_name,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE
        )
    return _WHISPER_CACHE[model_name]


# =======================
# UI / Orchestration (v1)
# =======================

@app.get("/", response_class=HTMLResponse)
def home(request: Request, user: str = Depends(auth)):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/tasks/review", response_class=HTMLResponse)
def tasks_review(request: Request, limit: int = 50, user: str = Depends(auth)):
    neo = _neo()
    with neo.driver.session() as s:
        res = s.run(
            """
            MATCH (s:Summary)-[:GENERATED_TASK]->(t:Task {status:'REVIEW'})
            RETURN t,s
            ORDER BY t.created_at LIMIT $limit
            """,
            {"limit": limit},
        )
        rows = [(dict(r[0]), dict(r[1])) for r in res]
    neo.close()
    enriched = []
    for t, s in rows:
        t["quality_score"] = s.get("quality_score")
        t["flags"] = s.get("flags") or []
        enriched.append(t)
    return templates.TemplateResponse("review.html", {"request": request, "tasks": enriched})

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
        res = s.run(
            """
            MATCH (t:Task {status:'READY'})
            OPTIONAL MATCH (t)-[:EXECUTED_BY]->(r:AgentRun)
            WITH t, r ORDER BY r.started_at DESC
            WITH t, collect(r)[0] AS lr
            OPTIONAL MATCH (lr)-[:USED_TOOL]->(k:ToolCall {tool:'acceptance'})
            RETURN t, k
            ORDER BY t.created_at
            LIMIT $limit
            """,
            {"limit": limit},
        )
        rows = [(dict(r[0]), (dict(r[1]) if r[1] else None)) for r in res]
    neo.close()
    enriched = []
    for t, k in rows:
        if k and k.get("output_json"):
            out = k["output_json"] if isinstance(k["output_json"], dict) else {}
            t["accept_status"] = "PASS" if out.get("passed") else "FAIL"
        else:
            t["accept_status"] = "—"
        enriched.append(t)
    return templates.TemplateResponse("ready.html", {"request": request, "tasks": enriched})

@app.post("/tasks/{task_id}/execute")
def execute_task(task_id: str, dry_run: bool = False, user: str = Depends(auth)):
    neo = _neo()
    with neo.driver.session() as s:
        rec = s.run("MATCH (t:Task{id:$id}) RETURN t", {"id": task_id}).single()
        if not rec:
            neo.close()
            raise HTTPException(status_code=404, detail="Task not found")
        t = dict(rec[0])
    neo.update_task_status(task_id, "RUNNING")
    try:
        result = run_task(neo, t, dry_run=dry_run)
        neo.update_task_status(task_id, "DONE")
        return JSONResponse({"status": "DONE", "task_id": task_id, "state": result})
    except Exception as e:
        neo.update_task_status(task_id, "FAILED")
        # Let FastAPI's default error handler produce the stack for logs
        raise
    finally:
        neo.close()

@app.post("/tasks/{task_id}/enqueue")
def enqueue_task(task_id: str, dry_run: bool = False, user: str = Depends(auth)):
    q = get_q()
    job = q.enqueue(execute_task_job, task_id, dry_run)
    EXECUTIONS.labels(status="ENQUEUED").inc()
    return {"enqueued": True, "job_id": job.get_id(), "task_id": task_id}

@app.get("/runs", response_class=HTMLResponse)
def runs(request: Request, limit: int = 50, user: str = Depends(auth)):
    neo = _neo()
    with neo.driver.session() as s:
        res = s.run("MATCH (r:AgentRun) RETURN r ORDER BY r.started_at DESC LIMIT $limit", {"limit": limit})
        rows = [dict(r[0]) for r in res]
    neo.close()
    return templates.TemplateResponse("runs.html", {"request": request, "runs": rows})

@app.get("/metrics")
def metrics(user: str = Depends(auth)):
    data = generate_latest()
    return PlainTextResponse(data.decode("utf-8"), media_type=CONTENT_TYPE_LATEST)


# =======================
# Ingestion (v2)
# =======================

@app.get("/ingest", response_class=HTMLResponse)
def ingest_ui(request: Request, user: str = Depends(auth)):
    # We don’t leak the token; we only tell the UI whether it’s required.
    token_required = bool(os.getenv("API_TOKEN"))
    return templates.TemplateResponse(
        "ingest.html",
        {
            "request": request,
            "token_required": token_required,
            "upload_endpoint": "/upload-audio",
            "suggested_models": ["tiny", "base", "small", "medium", "large-v3"],
        },
    )
@app.get("/health")
def health():
    return {"ok": True}

@app.post("/upload-audio")
async def upload_audio(
    file: UploadFile,
    model: str = Form("tiny"),
    x_api_token: Optional[str] = Header(default=None, convert_underscores=False),
):
    """
    Upload an audio file; transcribe with faster-whisper; persist JSON + TXT to disk;
    upsert (Transcription -> Segment) into Neo4j.

    Security:
      - If API_TOKEN env var is set, a matching 'x-api-token' header is required.
    """
    if API_TOKEN:
        if not x_api_token or x_api_token != API_TOKEN:
            raise HTTPException(status_code=401, detail="Unauthorized (missing/invalid API token)")

    # Save to temp, keeping original filename stem for output files
    tmp_path = pathlib.Path("/tmp") / f"{uuid.uuid4().hex}_{file.filename}"
    with open(tmp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Transcribe (cached model)
    wm = get_whisper_model(model)
    segments, info = wm.transcribe(str(tmp_path), beam_size=1)

    segs: List[Dict[str, Any]] = []
    stem = pathlib.Path(file.filename).stem
    for i, seg in enumerate(segments):
        segs.append({
            "id": f"{stem}_{i}",
            "idx": i,
            "start": round(seg.start or 0.0, 3),
            "end": round(seg.end or 0.0, 3),
            "text": (seg.text or "").strip(),
            "tokens_count": None
        })

    full_text = "\n".join(s["text"] for s in segs if s.get("text"))

    obj: Dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "key": stem,
        "text": full_text,
        "source_json": str((TRANSCRIPTIONS_ROOT / f"{stem}_transcription.json").resolve()),
        "source_rttm": None,
        "segments": segs,
        # you can include model/meta if useful downstream:
        "model": model,
        "language": getattr(info, "language", None),
    }

    # Persist JSON + TXT
    json_path = TRANSCRIPTIONS_ROOT / f"{stem}_transcription.json"
    txt_path = TRANSCRIPTIONS_ROOT / f"{stem}_transcription.txt"
    json_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(full_text, encoding="utf-8")

    # Upsert into Neo4j (Transcription + Segment graph)
    neo = _neo()
    try:
        neo.ingest_transcription(
            {
                "id": obj["id"],
                "key": obj["key"],
                "text": obj["text"],
                "source_json": obj["source_json"],
                "source_rttm": obj["source_rttm"],
                "embedding": None,  # attach later if you run embeddings
            },
            obj["segments"],
        )
    finally:
        neo.close()

    # Cleanup tmp
    tmp_path.unlink(missing_ok=True)

    return JSONResponse({
        "ok": True,
        "transcription_id": obj["id"],
        "segments": len(obj["segments"]),
        "json_path": obj["source_json"],
        "txt_path": str(txt_path),
        "model_used": model,
    })

@app.get("/api/transcriptions")
def api_list_transcriptions(
    q: Optional[str] = Query(None, description="text contains (case-insensitive)"),
    limit: int = Query(50, ge=1, le=500),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        with neo.driver.session() as s:
            if q:
                res = s.run(
                    """
                    MATCH (tr:Transcription)
                    WHERE toLower(tr.text) CONTAINS toLower($q)
                    RETURN tr
                    ORDER BY coalesce(tr.created_at_ts,0) DESC
                    LIMIT $limit
                    """,
                    {"q": q, "limit": limit},
                )
            else:
                res = s.run(
                    """
                    MATCH (tr:Transcription)
                    RETURN tr
                    ORDER BY coalesce(tr.created_at_ts,0) DESC
                    LIMIT $limit
                    """,
                    {"limit": limit},
                )
            items = [dict(r["tr"]) for r in res]
            return {"items": items, "count": len(items)}
    finally:
        neo.close()


@app.get("/api/transcriptions/{tid}")
def api_get_transcription(tid: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo.driver.session() as s:
            rec = s.run(
                """
                MATCH (tr:Transcription {id:$id})
                OPTIONAL MATCH (tr)<-[:ABOUT]-(t:Task)
                RETURN tr, collect(t) AS tasks
                """,
                {"id": tid},
            ).single()
            if not rec:
                raise HTTPException(status_code=404, detail="Transcription not found")
            tr = dict(rec["tr"])
            tasks = [dict(t) for t in rec["tasks"] if t]
            return {"transcription": tr, "tasks": tasks}
    finally:
        neo.close()

# ---------- Create Task from a transcription ----------
class CreateTaskIn(BaseModel):
    title: str
    status: str = "REVIEW"           # READY/REVIEW/RUNNING/DONE/FAILED
    kind: Optional[str] = "transcription_summary"
    payload: Optional[Dict[str, Any]] = None

@app.post("/api/transcriptions/{tid}/task")
def api_create_task_from_transcription(tid: str, body: CreateTaskIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo.driver.session() as s:
            has = s.run("MATCH (tr:Transcription {id:$id}) RETURN tr", {"id": tid}).single()
            if not has:
                raise HTTPException(status_code=404, detail="Transcription not found")

            res = s.run(
                """
                CREATE (t:Task {id:randomUUID()})
                SET t += $props,
                    t.created_at = datetime(), t.created_at_ts = timestamp()
                WITH t
                MATCH (tr:Transcription {id:$tid})
                MERGE (t)-[:ABOUT]->(tr)
                RETURN t.id AS id
                """,
                {
                    "props": {
                        "title": body.title,
                        "status": body.status,
                        "kind": body.kind,
                        "payload": body.payload or {},
                        "transcription_id": tid,
                    },
                    "tid": tid,
                },
            ).single()
            return {"task_id": res["id"]}
    finally:
        neo.close()

# ---------- Optional: enqueue an "embed" job as a Task ----------
@app.post("/api/transcriptions/{tid}/embed")
def api_embed_transcription(tid: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo.driver.session() as s:
            rec = s.run("MATCH (tr:Transcription {id:$id}) RETURN tr", {"id": tid}).single()
            if not rec:
                raise HTTPException(status_code=404, detail="Transcription not found")

            res = s.run(
                """
                CREATE (t:Task {id:randomUUID()})
                SET t.title='Embed transcription',
                    t.status='READY',
                    t.kind='embed_transcription',
                    t.transcription_id=$tid,
                    t.created_at=datetime(), t.created_at_ts=timestamp()
                WITH t
                MATCH (tr:Transcription {id:$tid})
                MERGE (t)-[:ABOUT]->(tr)
                RETURN t.id AS id
                """,
                {"tid": tid},
            ).single()
            return {"task_id": res["id"], "status": "READY"}
    finally:
        neo.close()

# ---------- TASKS: list/get (JSON) ----------
@app.get("/api/tasks")
def api_list_tasks(
    status: Optional[str] = Query(None, description="filter by status"),
    limit: int = Query(50, ge=1, le=500),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        with neo.driver.session() as s:
            if status:
                res = s.run(
                    """
                    MATCH (t:Task {status:$st})
                    RETURN t
                    ORDER BY coalesce(t.created_at_ts,0) DESC
                    LIMIT $limit
                    """,
                    {"st": status, "limit": limit},
                )
            else:
                res = s.run(
                    """
                    MATCH (t:Task)
                    RETURN t
                    ORDER BY coalesce(t.created_at_ts,0) DESC
                    LIMIT $limit
                    """,
                    {"limit": limit},
                )
            items = [dict(r["t"]) for r in res]
            return {"items": items, "count": len(items)}
    finally:
        neo.close()


@app.get("/api/tasks/{task_id}")
def api_get_task(task_id: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo.driver.session() as s:
            rec = s.run(
                """
                MATCH (t:Task {id:$id})
                OPTIONAL MATCH (t)-[:ABOUT]->(tr:Transcription)
                OPTIONAL MATCH (t)-[:EXECUTED_BY]->(r:AgentRun)
                RETURN t, tr, collect(r) AS runs
                """,
                {"id": task_id},
            ).single()
            if not rec:
                raise HTTPException(status_code=404, detail="Task not found")
            t = dict(rec["t"])
            tr = dict(rec["tr"]) if rec["tr"] else None
            runs = [dict(r) for r in rec["runs"] if r]
            return {"task": t, "transcription": tr, "runs": runs}
    finally:
        neo.close()

@app.post("/api/ask")
def api_ask(body: AskIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        neo.ensure_schema()  # safe
        out = answer_question(neo, body.question, model=body.model, max_repairs=body.max_repairs)
        return out
    finally:
        neo.close()

@app.post("/api/ask_async")
def api_ask_async(body: AskAsyncIn, user: str = Depends(auth)):
    """
    Enqueue a background Q&A job.
    Returns an answer_id you can poll at /api/answers/{answer_id}.
    """
    answer_id = new_answer_id()
    init_answer(answer_id, body.question, user_meta=body.meta)

    q = get_q()  # your existing RQ queue helper
    job = q.enqueue(ask_question_job, answer_id, body.question, body.model, body.max_repairs)
    set_status(answer_id, "QUEUED", job_id=job.get_id())

    return {"answer_id": answer_id, "job_id": job.get_id(), "status_url": f"/api/answers/{answer_id}"}

@app.get("/api/answers/{answer_id}")
def api_get_answer(answer_id: str, user: str = Depends(auth)):
    obj = get_answer(answer_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Answer not found")
    return obj