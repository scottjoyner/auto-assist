from __future__ import annotations

import os
import uuid
import json
import pathlib
import shutil
import hmac
import hashlib
import json, asyncio, os
import redis.asyncio as aioredis
from rq import Queue
import redis
import os, json, time, requests
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, Request, HTTPException, Depends, UploadFile, File, Form, Header, Query, Body, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import PlainTextResponse
from neo4j.exceptions import ServiceUnavailable
from .metrics import QA_REQUESTS, JOBS_ENQUEUED, TASK_CLAIMS, TASK_COMPLETIONS, TASK_HEARTBEATS, CONTEXT_PACKETS
from .metrics import RQ_JOBS_IN_QUEUE, RQ_JOBS_RUNNING, RQ_JOBS_FAILED
from .idempotency_store import save as idemp_save, load as idemp_load
from .neo4j_client import Neo4jClient  # unified client
from .paperclip_client import PaperclipClient
from .agents.orchestrator import *
from .pipeline import *
from .queue import *
from .jobs import *
from .metrics import EXECUTIONS
from .answers_store import get_answer, _chan as _answer_channel
from .answers_store import _global_chan
# add this near your other imports
try:
    from . import answers_store
    chan = answers_store._global_chan()
except ImportError:
    import assistx.answers_store as answers_store
    chan = answers_store._global_chan()

# from .answers_store import *

class AskAsyncIn(BaseModel):
    question: str
    model: str | None = None
    max_repairs: int = 3
    meta: dict | None = None
    idempotency_key: str | None = None   # <--- NEW

class AskIn(BaseModel):
    question: str
    model: str | None = None
    max_repairs: int = 3
    mode: str = "auto"
    timeout_s: float = 8.0
    idempotency_key: str | None = None   # <--- NEW

class IntentIn(BaseModel):
    source: str
    text: str
    idempotency_key: str | None = None
    client_ts: str | None = None
    metadata: Optional[Dict[str, Any]] = None

class ContextPacketIn(BaseModel):
    query: str
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    max_items: int = 20
    include_sources: Optional[List[str]] = None

class DispatchTarget(BaseModel):
    paperclip_agent_id: Optional[str] = None
    paperclip_issue_id: Optional[str] = None
    capabilities: Optional[List[str]] = None

class DispatchIn(BaseModel):
    task_id: str
    target: DispatchTarget
    priority: str = "MEDIUM"
    idempotency_key: Optional[str] = None

class TicketIn(BaseModel):
    title: str
    ticket_type: str = "task"
    status: str = "READY"
    kind: Optional[str] = None
    parent_id: Optional[str] = None
    required_capabilities: Optional[List[str]] = None
    target_agent_id: Optional[str] = None
    priority: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None

class TaskClaimIn(BaseModel):
    agent_id: str
    capabilities: Optional[List[str]] = None
    session_id: Optional[str] = None
    idempotency_key: Optional[str] = None

class TaskHeartbeatIn(BaseModel):
    agent_id: str
    status: Optional[str] = None
    session_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class TaskCompleteIn(BaseModel):
    agent_id: str
    status: str = "DONE"
    summary: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    idempotency_key: Optional[str] = None

class PaperclipEventIn(BaseModel):
    event_type: str
    paperclip_issue_id: str
    paperclip_agent_id: Optional[str] = None
    paperclip_run_id: Optional[str] = None
    event_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)

class MemoryWriteIn(BaseModel):
    kind: str
    text: str
    source: str
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class SignalEventIn(BaseModel):
    event_id: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None
    paperclip_issue_id: Optional[str] = None
    paperclip_run_id: Optional[str] = None

class SessionUpdateIn(BaseModel):
    paperclip_agent_id: Optional[str] = None
    hermes_session_id: Optional[str] = None
    agent_identity: Optional[str] = None
    device_id: Optional[str] = None
    platform: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

# -----------------------
# Config / Security
# -----------------------
security = HTTPBasic()
USER = os.getenv("BASIC_AUTH_USER", "neo4j")
PASS = os.getenv("BASIC_AUTH_PASS", "livelongandprosper")

API_TOKEN: Optional[str] = os.getenv("API_TOKEN")  # If set, required for /upload-audio
PAPERCLIP_WEBHOOK_SECRET: Optional[str] = os.getenv("PAPERCLIP_WEBHOOK_SECRET")

TRANSCRIPTIONS_ROOT = pathlib.Path(os.getenv("TRANSCRIPTIONS_ROOT", "./transcriptions")).resolve()
TRANSCRIPTIONS_ROOT.mkdir(parents=True, exist_ok=True)
CAPTURES_ROOT = pathlib.Path(os.getenv("CAPTURES_ROOT", "./artifacts/captures")).resolve()
CAPTURES_ROOT.mkdir(parents=True, exist_ok=True)

WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")        # e.g., "cuda", "cpu", "auto"
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE_TYPE", "int8") # e.g., "float16", "int8"
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_rconn = redis.from_url(REDIS_URL)
_q = Queue(connection=_rconn)
def _sse_format(event: str, data: dict | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"
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


@app.middleware("http")
async def neo4j_guard(request, call_next):
    try:
        return await call_next(request)
    except ServiceUnavailable:
        return PlainTextResponse("Neo4j unavailable. In host mode, set NEO4J_URI=bolt://host.docker.internal:7687 and add extra_hosts.", status_code=503)
    except ValueError as e:
        if "Cannot resolve address" in str(e):
            return PlainTextResponse("Neo4j hostname not resolvable from container. Use host.docker.internal (with host-gateway) or run neo4j in Compose.", status_code=503)
        raise

@app.on_event("startup")
def _startup():
    # create constraints on boot
    try:
        neo = Neo4jClient()
        neo.ensure_schema()
        neo.close()
    except Exception:
        pass
    
def auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not (credentials.username == USER and credentials.password == PASS):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


def _neo() -> Neo4jClient:
    neo = Neo4jClient()
    return neo

_paperclip_client: Optional[PaperclipClient] = None

def get_paperclip_client() -> Optional[PaperclipClient]:
    global _paperclip_client
    if _paperclip_client is not None:
        return _paperclip_client
    try:
        _paperclip_client = PaperclipClient()
        return _paperclip_client
    except ValueError:
        return None

def _verify_optional_paperclip_signature(body: BaseModel, signature: Optional[str]) -> None:
    if not PAPERCLIP_WEBHOOK_SECRET:
        return
    if not signature:
        raise HTTPException(status_code=401, detail="Missing Paperclip signature")
    payload = body.model_dump_json(exclude_none=True).encode("utf-8")
    expected = hmac.new(PAPERCLIP_WEBHOOK_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    accepted = {expected, f"sha256={expected}"}
    if not any(hmac.compare_digest(signature, candidate) for candidate in accepted):
        raise HTTPException(status_code=401, detail="Invalid Paperclip signature")


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







def _sse(event: str, data: dict | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"



class LLMStreamIn(BaseModel):
    prompt: Optional[str] = None                          # one-shot prompt
    messages: Optional[List[Dict[str, str]]] = None       # chat format: [{"role":"user","content":"..."}]
    model: Optional[str] = None                           # defaults to OLLAMA_MODEL env
    options: Optional[Dict[str, Any]] = None              # temperature, top_p, etc.
    system: Optional[str] = Field(default=None, description="Optional system instruction")








# =======================
# Startup
# =======================
@app.on_event("startup")
def _startup():
    # Don’t block server startup if Neo4j is down
    try:
        neo = Neo4jClient()
        # optional: only try if you *want* to bootstrap
        neo.ensure_schema()
    except Exception as e:
        # log; keep going so UI can load
        import logging
        logging.getLogger("uvicorn.error").warning(f"Neo4j not reachable at startup: {e}")
    finally:
        try:
            neo.close()
        except Exception:
            pass


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
    try:
        q = get_q()
        running_count = q.started_job_registry.count
        failed_count = q.failed_job_registry.count
        if callable(running_count):
            running_count = running_count()
        if callable(failed_count):
            failed_count = failed_count()
        RQ_JOBS_IN_QUEUE.set(len(q))
        RQ_JOBS_RUNNING.set(running_count)
        RQ_JOBS_FAILED.set(failed_count)
    except Exception:
        pass
    data = generate_latest()
    return PlainTextResponse(data.decode("utf-8"), media_type=CONTENT_TYPE_LATEST)

@app.get("/answers", response_class=HTMLResponse)
def answers_dashboard(request: Request, user: str = Depends(auth)):
    return templates.TemplateResponse("answers.html", {"request": request})

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

def _safe_upload_name(name: str, fallback: str = "capture") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {".", "_", "-"} else "_" for ch in (name or fallback))
    return cleaned.strip("._") or fallback

def _media_kind(content_type: str, filename: str) -> str:
    value = (content_type or "").lower()
    suffix = pathlib.Path(filename or "").suffix.lower()
    if value.startswith("video/") or suffix in {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}:
        return "video"
    if value.startswith("audio/") or suffix in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}:
        return "audio"
    if value.startswith("image/") or suffix in {".jpg", ".jpeg", ".png", ".heic", ".webp"}:
        return "image"
    return "media"

def _json_object(value: str) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    return parsed if isinstance(parsed, dict) else {"value": parsed}

@app.post("/api/captures")
async def api_create_capture(
    request: Request,
    media: UploadFile | None = File(default=None),
    file: UploadFile | None = File(default=None),
    transcript: str = Form(default=""),
    user_id: str = Form(default="default"),
    session_id: str = Form(default="mobile"),
    duration_ms: int = Form(default=0),
    device_id: str = Form(default=""),
    device_fingerprint: str = Form(default=""),
    client_context: str = Form(default=""),
    activity_context: str = Form(default=""),
    user: str = Depends(auth),
):
    upload = media or file
    capture_id = uuid.uuid4().hex
    media_path = ""
    filename = ""
    byte_count = 0
    content_type = upload.content_type if upload else "text/plain"
    if upload is not None:
        filename = _safe_upload_name(upload.filename or f"{capture_id}.bin")
        suffix = pathlib.Path(filename).suffix or ".bin"
        stored_name = f"{capture_id}{suffix.lower()}"
        target = CAPTURES_ROOT / stored_name
        with target.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)
        media_path = str(target)
        byte_count = target.stat().st_size
    context = _json_object(client_context)
    headers = request.headers
    context.update(
        {
            "device_id": device_id or context.get("device_id", ""),
            "device_fingerprint": device_fingerprint or context.get("device_fingerprint", ""),
            "client_ip": headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else ""),
            "user_agent": headers.get("user-agent", context.get("user_agent", "")),
            "language": headers.get("accept-language", context.get("language", "")),
            "activity_context": activity_context or context.get("activity_context", ""),
        }
    )
    kind = _media_kind(content_type, filename)
    neo = _neo()
    try:
        graph = neo.ingest_media_capture(
            capture_id=capture_id,
            user_id=user_id or user,
            session_id=session_id or "mobile",
            transcript=transcript,
            media_path=media_path,
            filename=filename,
            content_type=content_type,
            media_kind=kind,
            duration_ms=duration_ms,
            byte_count=byte_count,
            device_id=str(context.get("device_id") or ""),
            device_fingerprint=str(context.get("device_fingerprint") or ""),
            activity_context=str(context.get("activity_context") or ""),
            client_context=context,
            metadata={"authenticated_user": user},
        )
    finally:
        neo.close()
    return {
        "ok": True,
        "capture_id": capture_id,
        "session_id": session_id or "mobile",
        "user_id": user_id or user,
        "media_path": media_path,
        "filename": filename,
        "bytes": byte_count,
        "content_type": content_type,
        "media_kind": kind,
        "duration_ms": duration_ms,
        "transcript_saved": bool(transcript.strip()),
        **graph,
    }

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

@app.get("/api/agent/tasks")
def api_agent_tasks(
    status: str = Query("READY", description="task status to poll"),
    capabilities: Optional[List[str]] = Query(None, description="agent capabilities"),
    agent_id: Optional[str] = Query(None, description="optional agent id for targeted tasks"),
    limit: int = Query(20, ge=1, le=100),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        items = neo.list_agent_tasks(
            status=status,
            capabilities=capabilities,
            agent_id=agent_id,
            limit=limit,
        )
        return {"items": items, "count": len(items)}
    finally:
        neo.close()

@app.post("/api/tasks/{task_id}/claim")
def api_claim_task(task_id: str, body: TaskClaimIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        result = neo.claim_task(
            task_id=task_id,
            agent_id=body.agent_id,
            capabilities=body.capabilities,
            session_id=body.session_id,
            idempotency_key=body.idempotency_key,
        )
        if result.get("claimed"):
            TASK_CLAIMS.labels(result="claimed").inc()
            return result
        if result.get("reason") == "not_found":
            raise HTTPException(status_code=404, detail="Task not found")
        TASK_CLAIMS.labels(result=result.get("reason", "conflict")).inc()
        raise HTTPException(status_code=409, detail=result)
    finally:
        neo.close()

@app.post("/api/tasks/{task_id}/heartbeat")
def api_heartbeat_task(task_id: str, body: TaskHeartbeatIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        task = neo.heartbeat_task(
            task_id=task_id,
            agent_id=body.agent_id,
            status=body.status,
            session_id=body.session_id,
            metadata=body.metadata,
        )
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        TASK_HEARTBEATS.labels(status=task.get("status", body.status or "unknown")).inc()
        return {"task": task}
    finally:
        neo.close()

@app.post("/api/tasks/{task_id}/complete")
def api_complete_task(task_id: str, body: TaskCompleteIn, user: str = Depends(auth)):
    if body.status not in {"DONE", "FAILED", "CANCELLED"}:
        raise HTTPException(status_code=400, detail="status must be DONE, FAILED, or CANCELLED")
    neo = _neo()
    try:
        task = neo.complete_task(
            task_id=task_id,
            agent_id=body.agent_id,
            status=body.status,
            summary=body.summary,
            result=body.result,
            session_id=body.session_id,
            idempotency_key=body.idempotency_key,
        )
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        TASK_COMPLETIONS.labels(status=body.status).inc()
        return {"task": task}
    finally:
        neo.close()

@app.post("/api/tickets")
def api_create_ticket(body: TicketIn, user: str = Depends(auth)):
    if body.status not in {"READY", "CLAIMED", "RUNNING", "DONE", "FAILED", "CANCELLED", "REVIEW"}:
        raise HTTPException(status_code=400, detail="Unsupported ticket status")
    if body.ticket_type not in {"deliverable", "epic", "story", "task", "bug", "chore"}:
        raise HTTPException(status_code=400, detail="ticket_type must be deliverable, epic, story, task, bug, or chore")
    neo = _neo()
    try:
        ticket_id = neo.upsert_ticket(
            title=body.title,
            ticket_type=body.ticket_type,
            status=body.status,
            kind=body.kind,
            parent_id=body.parent_id,
            required_capabilities=body.required_capabilities,
            target_agent_id=body.target_agent_id,
            priority=body.priority,
            payload=body.payload,
            idempotency_key=body.idempotency_key,
        )
        return {"ticket_id": ticket_id}
    finally:
        neo.close()

@app.get("/api/tickets/{ticket_id}/tree")
def api_get_ticket_tree(ticket_id: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        tree = neo.get_ticket_tree(ticket_id)
        if not tree:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return tree
    finally:
        neo.close()

@app.post("/api/intents")
def api_create_intent(body: IntentIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        intent_id = neo.upsert_intent(
            source=body.source,
            text=body.text,
            idempotency_key=body.idempotency_key,
            client_ts=body.client_ts,
            metadata=body.metadata,
        )
        return {"intent_id": intent_id}
    finally:
        neo.close()

@app.post("/api/brain/context")
def api_create_context_packet(body: ContextPacketIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        packet = neo.create_context_packet(
            query=body.query,
            task_id=body.task_id,
            session_id=body.session_id,
            max_items=body.max_items,
            include_sources=body.include_sources or ["memory", "knowledge", "orchestration"],
        )
        CONTEXT_PACKETS.inc()
        return {"context_packet": packet}
    finally:
        neo.close()

@app.get("/api/context-packets/{packet_id}")
def api_get_context_packet(packet_id: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        packet = neo.get_context_packet(packet_id)
        if not packet:
            raise HTTPException(status_code=404, detail="ContextPacket not found")
        return {"context_packet": packet}
    finally:
        neo.close()

@app.post("/api/dispatch")
def api_create_dispatch(body: DispatchIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        pc = get_paperclip_client()
        result = neo.create_dispatch_with_paperclip(
            task_id=body.task_id,
            target=body.target.model_dump(),
            priority=body.priority,
            idempotency_key=body.idempotency_key,
            paperclip_client=pc,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        neo.close()

@app.post("/api/paperclip/events")
def api_paperclip_event(
    body: PaperclipEventIn,
    user: str = Depends(auth),
    x_paperclip_signature: Optional[str] = Header(None),
):
    _verify_optional_paperclip_signature(body, x_paperclip_signature)
    neo = _neo()
    try:
        issue_id = neo.ingest_paperclip_event(
            event_type=body.event_type,
            paperclip_issue_id=body.paperclip_issue_id,
            paperclip_agent_id=body.paperclip_agent_id,
            paperclip_run_id=body.paperclip_run_id,
            event_id=body.event_id,
            payload=body.payload,
        )
        return {"paperclip_issue_id": issue_id}
    finally:
        neo.close()

@app.post("/api/memory/items")
def api_write_memory(body: MemoryWriteIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        memory_id = neo.upsert_memory_item(
            kind=body.kind,
            text=body.text,
            source=body.source,
            session_id=body.session_id,
            task_id=body.task_id,
            metadata=body.metadata,
        )
        return {"memory_item_id": memory_id}
    finally:
        neo.close()

@app.post("/api/brain/signals")
def api_create_signal_event(body: SignalEventIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        event_id = neo.create_signal_event(
            event_id=body.event_id,
            event_type=body.event_type,
            payload=body.payload,
            session_id=body.session_id,
            paperclip_issue_id=body.paperclip_issue_id,
            paperclip_run_id=body.paperclip_run_id,
        )
        return {"signal_event_id": event_id}
    finally:
        neo.close()

@app.post("/api/sessions/{session_id}")
def api_update_session(session_id: str, body: SessionUpdateIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        updated_id = neo.upsert_agent_session(
            session_id=session_id,
            paperclip_agent_id=body.paperclip_agent_id,
            hermes_session_id=body.hermes_session_id,
            agent_identity=body.agent_identity,
            device_id=body.device_id,
            platform=body.platform,
            metadata=body.metadata,
        )
        return {"session_id": updated_id}
    finally:
        neo.close()

@app.get("/api/dispatches")
def api_list_dispatches(
    status: Optional[str] = Query(None, description="Dispatch status filter"),
    limit: int = Query(50, ge=1, le=500),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        items = neo.list_dispatches(status=status, limit=limit)
        return {"items": items, "count": len(items)}
    finally:
        neo.close()

@app.get("/api/sessions")
def api_list_sessions(
    limit: int = Query(50, ge=1, le=500),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        items = neo.list_agent_sessions(limit=limit)
        return {"items": items, "count": len(items)}
    finally:
        neo.close()

# ---------- Phase 4: Command Center APIs ----------

@app.get("/api/intents")
def api_list_intents(
    source: Optional[str] = Query(None, description="filter by source"),
    limit: int = Query(50, ge=1, le=500),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        with neo.driver.session() as s:
            if source:
                res = s.run(
                    "MATCH (i:Intent {source:$source}) "
                    "RETURN i ORDER BY i.created_at_ts DESC LIMIT $limit",
                    {"source": source, "limit": limit},
                )
            else:
                res = s.run(
                    "MATCH (i:Intent) RETURN i ORDER BY i.created_at_ts DESC LIMIT $limit",
                    {"limit": limit},
                )
            items = [dict(r["i"]) for r in res]
            return {"items": items, "count": len(items)}
    finally:
        neo.close()

@app.get("/api/intents/{intent_id}")
def api_get_intent(intent_id: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo.driver.session() as s:
            rec = s.run(
                "MATCH (i:Intent {id:$id}) "
                "OPTIONAL MATCH (i)-[:CREATED_TASK]->(t:Task) "
                "RETURN i, collect(t) AS tasks",
                {"id": intent_id},
            ).single()
            if not rec:
                raise HTTPException(status_code=404, detail="Intent not found")
            intent = dict(rec["i"])
            tasks = [dict(t) for t in rec["tasks"] if t]
            return {"intent": intent, "tasks": tasks}
    finally:
        neo.close()

@app.get("/api/devices")
def api_list_devices(
    limit: int = Query(50, ge=1, le=500),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        items = neo.list_agent_devices(limit=limit)
        return {"items": items, "count": len(items)}
    finally:
        neo.close()

@app.get("/api/devices/{device_id}")
def api_get_device(device_id: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        device = neo.get_agent_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        with neo.driver.session() as s:
            sessions = s.run(
                "MATCH (s:AgentSession) WHERE s.device_id=$did "
                "RETURN s ORDER BY s.updated_at_ts DESC LIMIT 10",
                {"did": device_id},
            )
            agent_sessions = [dict(s["s"]) for s in sessions]
        return {"device": device, "agent_sessions": agent_sessions}
    finally:
        neo.close()

@app.get("/api/memory")
def api_list_memory(
    kind: Optional[str] = Query(None, description="filter by memory kind"),
    source: Optional[str] = Query(None, description="filter by source"),
    limit: int = Query(50, ge=1, le=500),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        with neo.driver.session() as s:
            q = "MATCH (m:MemoryItem)"
            params = {"limit": limit}
            conditions = []
            if kind:
                conditions.append("m.kind=$kind")
                params["kind"] = kind
            if source:
                conditions.append("m.source=$source")
                params["source"] = source
            if conditions:
                q += " WHERE " + " AND ".join(conditions)
            q += " RETURN m ORDER BY m.updated_at_ts DESC LIMIT $limit"
            res = s.run(q, params)
            items = [dict(r["m"]) for r in res]
            return {"items": items, "count": len(items)}
    finally:
        neo.close()

@app.get("/api/memory/{memory_id}")
def api_get_memory(memory_id: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo.driver.session() as s:
            rec = s.run(
                "MATCH (m:MemoryItem {id:$id}) "
                "OPTIONAL MATCH (m)<-[:WROTE_MEMORY]-(s:AgentSession) "
                "OPTIONAL MATCH (m)<-[:RELATED_MEMORY]-(t:Task) "
                "RETURN m, collect(DISTINCT s) AS sessions, collect(DISTINCT t) AS tasks",
                {"id": memory_id},
            ).single()
            if not rec:
                raise HTTPException(status_code=404, detail="Memory not found")
            memory = dict(rec["m"])
            sessions = [dict(s) for s in rec["sessions"] if s]
            tasks = [dict(t) for t in rec["tasks"] if t]
            return {"memory": memory, "sessions": sessions, "tasks": tasks}
    finally:
        neo.close()

@app.post("/api/tasks/{task_id}/cancel")
def api_cancel_task(task_id: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        neo.update_task_status(task_id, "CANCELLED")
        return {"task_id": task_id, "status": "CANCELLED"}
    finally:
        neo.close()

@app.post("/api/tasks/{task_id}/pause")
def api_pause_task(task_id: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo.driver.session() as s:
            s.run(
                "MATCH (t:Task {id:$id}) SET t.paused=true, t.paused_at=datetime(), t.paused_at_ts=timestamp()",
                {"id": task_id},
            )
        return {"task_id": task_id, "paused": True}
    finally:
        neo.close()

@app.post("/api/tasks/{task_id}/resume")
def api_resume_task(task_id: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo.driver.session() as s:
            s.run(
                "MATCH (t:Task {id:$id}) SET t.paused=false, t.resumed_at=datetime(), t.resumed_at_ts=timestamp()",
                {"id": task_id},
            )
        return {"task_id": task_id, "paused": False}
    finally:
        neo.close()

@app.post("/api/dispatches/{dispatch_id}/reassign")
def api_reassign_dispatch(dispatch_id: str, target: DispatchTarget, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo.driver.session() as s:
            s.run(
                "MATCH (d:Dispatch {id:$id}) "
                "SET d.paperclip_agent_id=$agent_id, d.updated_at=datetime(), d.updated_at_ts=timestamp()",
                {"id": dispatch_id, "agent_id": target.paperclip_agent_id},
            )
            if target.paperclip_agent_id:
                s.run(
                    "MERGE (a:AgentSession {paperclip_agent_id:$aid}) "
                    "ON CREATE SET a.id=randomUUID(), a.created_at=datetime(), a.created_at_ts=timestamp() "
                    "MERGE (d:Dispatch {id:$did})-[:ASSIGNED_TO]->(a)",
                    {"aid": target.paperclip_agent_id, "did": dispatch_id},
                )
        return {"dispatch_id": dispatch_id, "reassigned": True}
    finally:
        neo.close()

@app.post("/api/ask_async")
def api_ask_async(body: AskAsyncIn, user: str = Depends(auth)):
    # Idempotency: if key maps to an existing answer, return it
    if body.idempotency_key:
        hit = idemp_load(body.idempotency_key)
        if hit:
            # ensure the referenced answer still exists
            existing = answers_store.get_answer(hit.get("answer_id",""))
            if existing:
                return {"answer_id": existing["id"], "job_id": existing.get("job_id"), "status_url": f"/api/answers/{existing['id']}", "idempotent": True, **(existing.get("meta") or {})}

    answer_id = answers_store.new_answer_id()
    neo = _neo()
    try:
        deliverable = neo.create_deliverable_from_ask(
            question=body.question,
            answer_id=answer_id,
            mode="async",
            user=user,
            idempotency_key=body.idempotency_key or answer_id,
        )
    finally:
        neo.close()
    meta = {**(body.meta or {}), **deliverable}
    answers_store.init_answer(answer_id, body.question, user_meta=meta)
    q = get_q()
    job = q.enqueue(ask_question_job, answer_id, body.question, body.model, body.max_repairs, deliverable["deliverable_id"])
    answers_store.set_status(answer_id, "QUEUED", job_id=job.get_id())

    if body.idempotency_key:
        idemp_save(body.idempotency_key, {"answer_id": answer_id})

    JOBS_ENQUEUED.inc()
    return {"answer_id": answer_id, "job_id": job.get_id(), "status_url": f"/api/answers/{answer_id}", **deliverable}

@app.post("/api/ask")
def api_ask(body: AskIn, user: str = Depends(auth)):
    mode = (body.mode or "auto").lower()
    if mode not in ("sync", "async", "auto"):
        raise HTTPException(status_code=400, detail="mode must be one of: sync, async, auto")

    # Idempotency in auto/async: reuse existing answer if present
    if body.idempotency_key and mode in ("async", "auto"):
        hit = idemp_load(body.idempotency_key)
        if hit:
            existing = answers_store.get_answer(hit.get("answer_id",""))
            if existing:
                if mode == "async":
                    return JSONResponse(status_code=202, content={"answer_id": existing["id"], "job_id": existing.get("job_id"), "status_url": f"/api/answers/{existing['id']}", "idempotent": True, **(existing.get("meta") or {})})
                # mode auto: if already DONE return data, else return 202
                if existing.get("status") == "DONE" and existing.get("data"):
                    return existing["data"]
                return JSONResponse(status_code=202, content={"answer_id": existing["id"], "job_id": existing.get("job_id"), "status_url": f"/api/answers/{existing['id']}", "status": existing.get("status"), "idempotent": True, **(existing.get("meta") or {})})

    if mode == "sync":
        QA_REQUESTS.labels(mode="sync", status="started").inc()
        neo = _neo()
        deliverable = None
        try:
            deliverable = neo.create_deliverable_from_ask(
                question=body.question,
                answer_id=None,
                mode="sync",
                user=user,
                idempotency_key=body.idempotency_key,
            )
            out = answer_question(neo, body.question, model=body.model, max_repairs=body.max_repairs, log_to_neo=True)
            completed = neo.complete_deliverable(
                deliverable_id=deliverable["deliverable_id"],
                status="DONE",
                summary=out.get("answer"),
                result=out,
            )
            out.update(deliverable)
            out["deliverable_status"] = completed.get("status") if completed else "UNKNOWN"
            QA_REQUESTS.labels(mode="sync", status="done").inc()
            return out
        except Exception as e:
            if deliverable:
                try:
                    neo.complete_deliverable(
                        deliverable_id=deliverable["deliverable_id"],
                        status="FAILED",
                        summary=str(e),
                        result={"error": str(e)},
                    )
                except Exception:
                    pass
            QA_REQUESTS.labels(mode="sync", status="failed").inc()
            raise
        finally:
            neo.close()

    # async/auto enqueue
    answer_id = answers_store.new_answer_id()
    neo = _neo()
    try:
        deliverable = neo.create_deliverable_from_ask(
            question=body.question,
            answer_id=answer_id,
            mode=mode,
            user=user,
            idempotency_key=body.idempotency_key or answer_id,
        )
    finally:
        neo.close()
    answers_store.init_answer(answer_id, body.question, user_meta={"mode": mode, **deliverable})
    q = get_q()
    job = q.enqueue(ask_question_job, answer_id, body.question, body.model, body.max_repairs, deliverable["deliverable_id"])
    answers_store.set_status(answer_id, "QUEUED", job_id=job.get_id())
    JOBS_ENQUEUED.inc()
    if body.idempotency_key:
        idemp_save(body.idempotency_key, {"answer_id": answer_id})

    if mode == "async":
        return JSONResponse(status_code=202, content={"answer_id": answer_id, "job_id": job.get_id(), "status_url": f"/api/answers/{answer_id}", **deliverable})

    # mode == auto: wait budget, else 202
    import time as _time
    deadline = _time.time() + max(0.0, float(body.timeout_s))
    while _time.time() < deadline:
        obj = answers_store.get_answer(answer_id)
        if obj and obj.get("status") in ("DONE", "FAILED"):
            if obj.get("status") == "DONE" and obj.get("data"):
                return obj["data"]
            return JSONResponse(status_code=200, content=obj)
        _time.sleep(0.25)
    return JSONResponse(status_code=202, content={"answer_id": answer_id, "job_id": job.get_id(), "status": "PENDING", "status_url": f"/api/answers/{answer_id}", **deliverable})


@app.get("/api/answers/{answer_id}")
def api_get_answer(answer_id: str, user: str = Depends(auth)):
    obj = answers_store.get_answer(answer_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Answer not found")
    return obj

@app.get("/api/answers")
def api_list_answers(
    status: str | None = Query(None, description="QUEUED|RUNNING|DONE|FAILED"),
    q: str | None = Query(None, description="Substring in question"),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, description="Opaque cursor: '<score>:<id>' from previous page"),
    user: str = Depends(auth),
):
    return answers_store.list_answers_paginated(status=status, q=q, limit=limit, cursor=cursor)

@app.post("/api/answers/reindex")
def api_answers_reindex(user: str = Depends(auth)):
    return answers_store.rebuild_index()

@app.websocket("/ws/answers/{answer_id}")
async def ws_answer_events(websocket: WebSocket, answer_id: str):
    # Basic Auth doesn't apply to WS; do a simple token if you want. For now, accept all.
    await websocket.accept()
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    chan = _answer_channel(answer_id)
    await pubsub.subscribe(chan)

    # initial payload
    await websocket.send_text(json.dumps({"type": "init", "data": answers_store.get_answer(answer_id) or {"id": answer_id, "status": "UNKNOWN"}}))

    try:
        last_ping = asyncio.get_event_loop().time()
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            now = asyncio.get_event_loop().time()

            if msg and msg.get("type") == "message":
                await websocket.send_text(msg["data"])

            # keepalive ping every 15s
            if now - last_ping > 15:
                await websocket.send_text(json.dumps({"type": "ping", "ts": int(now)}))
                last_ping = now

            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await pubsub.unsubscribe(chan)
        except Exception:
            pass
        await pubsub.close()
        await r.aclose()
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/ws/answers")
async def ws_answers(websocket: WebSocket):
    await websocket.accept()
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    chan = answers_store._global_chan()     # <— FIX: use module
    await pubsub.subscribe(chan)

    try:
        await websocket.send_text(json.dumps({"type": "welcome", "channel": chan}))
        last_ping = asyncio.get_event_loop().time()
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            now = asyncio.get_event_loop().time()

            if msg and msg.get("type") == "message":
                await websocket.send_text(msg["data"])  # already JSON

            if now - last_ping > 15:
                await websocket.send_text(json.dumps({"type": "ping", "ts": int(now)}))
                last_ping = now

            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        pass
    finally:
        try: await pubsub.unsubscribe(chan)
        except Exception: pass
        await pubsub.close()
        await r.aclose()
        try: await websocket.close()
        except Exception: pass

@app.get("/api/answers/events")
async def api_answers_events(
    status: str | None = Query(None, description="Optional filter hint; client can also filter"),
    user: str = Depends(auth),
):
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    chan = answers_store._global_chan()
    await pubsub.subscribe(chan)

    async def event_stream():
        # initial hello
        yield _sse_format("welcome", {"channel": chan})
        last_ping = asyncio.get_event_loop().time()
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                now = asyncio.get_event_loop().time()

                if msg and msg.get("type") == "message":
                    try:
                        data = json.loads(msg["data"])  # {"type":"new|update","data":{...}}
                    except Exception:
                        data = {"type": "update", "data": msg["data"]}
                    if status and data.get("data", {}).get("status") != status:
                        pass
                    else:
                        yield _sse_format(data.get("type", "update"), data.get("data", {}))

                # keepalive
                if now - last_ping > 15:
                    yield _sse_format("ping", {"ts": int(now)})
                    last_ping = now

                await asyncio.sleep(0.2)
        finally:
            try:
                await pubsub.unsubscribe(chan)
            except Exception:
                pass
            await pubsub.close()
            await r.aclose()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/answers/{answer_id}/events")
async def api_answer_events(answer_id: str, user: str = Depends(auth)):
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    chan = answers_store._chan(answer_id)
    await pubsub.subscribe(chan)

    async def stream():
        # initial snapshot so the client shows current status immediately
        snap = answers_store.get_answer(answer_id)
        if snap:
            yield _sse_format("init", {"status": snap.get("status"), "data": snap.get("data"), "error": snap.get("error")})

        last_ping = asyncio.get_event_loop().time()
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                now = asyncio.get_event_loop().time()

                if msg and msg.get("type") == "message":
                    try:
                        payload = json.loads(msg["data"])  # {"type":"new|update","data":{...}}
                    except Exception:
                        payload = {"type": "update", "data": msg["data"]}
                    yield _sse_format(payload.get("type", "update"), payload.get("data", {}))

                if now - last_ping > 15:
                    yield _sse_format("ping", {"ts": int(now)})
                    last_ping = now

                await asyncio.sleep(0.2)
        finally:
            try: await pubsub.unsubscribe(chan)
            except: pass
            await pubsub.close()
            await r.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream")

@app.post("/api/llm/stream")
def llm_stream(body: LLMStreamIn, user: str = Depends(auth)):
    """
    Proxies Ollama /api/chat with stream=true and re-emits as SSE:
      - 'model'  : the model name (defaults to OLLAMA_MODEL)
      - 'delta'  : token/partial text chunks (safe: not persisted)
      - 'done'   : final stats (no output text)
      - 'error'  : error info if upstream fails
    """
    model = body.model or os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    if not model:
        return StreamingResponse(iter([_sse("error", {"error": "No model configured"})]),
                                 media_type="text/event-stream")

    # Build Ollama payload
    msgs: List[Dict[str, str]] = []
    if body.system:
        msgs.append({"role": "system", "content": body.system})
    if body.messages and len(body.messages) > 0:
        msgs.extend(body.messages)
    elif body.prompt:
        msgs.append({"role": "user", "content": body.prompt})
    else:
        return StreamingResponse(iter([_sse("error", {"error": "Provide 'prompt' or 'messages'"})]),
                                 media_type="text/event-stream")

    payload = {
        "model": model,
        "messages": msgs,
        "stream": True,
    }
    if body.options:
        payload["options"] = body.options

    # Stream from Ollama using requests (no new deps)
    def gen():
        url = f"{OLLAMA_HOST}/api/chat"
        try:
            with requests.post(url, json=payload, stream=True, timeout=(5, 600)) as r:
                r.raise_for_status()
                yield _sse("model", {"model": model})

                buf_total = 0
                for raw in r.iter_lines(decode_unicode=True):
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except Exception:
                        # Defensive: forward unparsed line
                        yield _sse("delta", raw)
                        continue

                    # Standard Ollama chat stream fields:
                    # { "message": {"role":"assistant","content":"...partial..."}, "done": false, ... }
                    if data.get("message") and isinstance(data["message"], dict):
                        piece = data["message"].get("content") or ""
                        if piece:
                            buf_total += len(piece)
                            # stream just the token/partial to the UI
                            yield _sse("delta", piece)

                    if data.get("done"):
                        stats = {
                            "total_ms": data.get("total_duration"),
                            "eval_count": data.get("eval_count"),
                            "prompt_eval_count": data.get("prompt_eval_count"),
                        }
                        yield _sse("done", {k: v for k, v in stats.items() if v is not None})
                        break
        except requests.HTTPError as e:
            yield _sse("error", {"error": f"HTTP {e.response.status_code}", "detail": e.response.text[:500]})
        except requests.RequestException as e:
            yield _sse("error", {"error": "Upstream unreachable", "detail": str(e)})

    # Important: we do NOT persist streamed tokens; this is transient UI-only.
    return StreamingResponse(gen(), media_type="text/event-stream")
