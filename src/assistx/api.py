from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import pathlib
import shutil
import threading
import time as _time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import requests
from fastapi import (Body, Depends, FastAPI, File, Form, Header, HTTPException,
                     Query, Request, UploadFile, WebSocket, WebSocketDisconnect)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, StreamingResponse)
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from neo4j.exceptions import ServiceUnavailable
from pydantic import BaseModel, ConfigDict, Field
from .deps import load_aioredis_module, load_prometheus_client, load_queue_class, load_redis_module, multipart_available
from .logging_utils import install_logging_middleware, setup_logging
from .runtime import build_runtime_health, runtime_profile, validate_runtime_configuration

CONTENT_TYPE_LATEST, generate_latest = load_prometheus_client()
redis = load_redis_module()
aioredis = load_aioredis_module()
Queue = load_queue_class()
from .metrics import QA_REQUESTS, JOBS_ENQUEUED, TASK_CLAIMS, TASK_COMPLETIONS, TASK_HEARTBEATS, CONTEXT_PACKETS
from .metrics import RQ_JOBS_IN_QUEUE, RQ_JOBS_RUNNING, RQ_JOBS_FAILED
from .metrics import REQUESTS
from .idempotency_store import save as idemp_save, load as idemp_load
from .neo4j_client import Neo4jClient  # unified client
from .paperclip_client import PaperclipClient
from .rate_limiter import DISPATCH_LIMITER, EVENT_LIMITER, ASK_LIMITER, INTENT_LIMITER
from .feed_registry import feed_health_summary
from .evaluation_registry import suites_summary
from .intent_classifier import (
    classify_text,
    CLASSIFICATION_TASK,
    CLASSIFICATION_CANCEL,
    CLASSIFICATION_QUERY,
    CLASSIFICATION_MEMORY,
)
from .agents.orchestrator import run_task
from .pipeline.qa_pipeline import answer_question
from .queue import get_q
from .jobs import execute_task_job, ask_question_job
from .metrics import EXECUTIONS
from .answers_store import get_answer, _chan as _answer_channel
from .answers_store import _global_chan
from . import answers_store
chan = answers_store._global_chan()
from .swarm_core import record_trace_event

class AskAsyncIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question: str = Field(min_length=1, max_length=8000)
    model: str | None = None
    max_repairs: int = 3
    meta: dict | None = None
    idempotency_key: str | None = None   # <--- NEW

class AskIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question: str = Field(min_length=1, max_length=8000)
    model: str | None = None
    max_repairs: int = 3
    mode: str = "auto"
    timeout_s: float = 8.0
    idempotency_key: str | None = None   # <--- NEW

class IntentIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str
    text: str
    idempotency_key: str | None = None
    client_ts: str | None = None
    metadata: Optional[Dict[str, Any]] = None

class ContextPacketIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    max_items: int = 20
    include_sources: Optional[List[str]] = None

class DispatchTarget(BaseModel):
    model_config = ConfigDict(extra="ignore")

    paperclip_agent_id: Optional[str] = None
    paperclip_issue_id: Optional[str] = None
    capabilities: Optional[List[str]] = None

class DispatchIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str
    target: DispatchTarget
    priority: str = "MEDIUM"
    idempotency_key: Optional[str] = None

class TicketIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    model_config = ConfigDict(extra="ignore")

    agent_id: str
    capabilities: Optional[List[str]] = None
    session_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    lease_seconds: Optional[int] = None

class TaskHeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent_id: str
    status: Optional[str] = None
    session_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    lease_seconds: Optional[int] = None

class TaskCompleteIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent_id: str
    status: str = "DONE"
    summary: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class TaskCreateIn(BaseModel):
    """Create a swarm task that a capability-tagged fleet node can pick up.

    Used by auto-ingest (and any producer) to fan a batch/folder into
    READY tasks without touching Neo4j directly. The fleet node-agent polls
    ``GET /api/agent/tasks?capabilities=...`` and executes ``payload.command``
    or ``payload.yolo_command``.
    """

    model_config = ConfigDict(extra="ignore")

    task_id: Optional[str] = None
    title: str
    task_type: str = "swarm_task"
    status: str = "READY"
    kind: Optional[str] = None
    required_capabilities: Optional[List[str]] = None
    target_agent_id: Optional[str] = None
    priority: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None
    correlation_id: Optional[str] = None


class PaperclipEventIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_type: str
    paperclip_issue_id: str
    paperclip_agent_id: Optional[str] = None
    paperclip_run_id: Optional[str] = None
    event_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)

class MemoryWriteIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str
    text: str
    source: str
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class SignalEventIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None
    paperclip_issue_id: Optional[str] = None
    paperclip_run_id: Optional[str] = None

class VoiceEventIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str
    event_type: str
    text: Optional[str] = None
    source: str = "voice"
    session_id: Optional[str] = None
    client_ts: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    auto_dispatch: bool = True


class SophiaVoiceEventIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str
    event_type: str
    session_id: Optional[str] = None
    transcript_text: Optional[str] = None
    auth_state: Optional[str] = None
    speaker_identity: Optional[str] = None
    speaker_confidence: Optional[float] = None
    policy_version: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Optional[Dict[str, Any]] = None

class SessionUpdateIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    paperclip_agent_id: Optional[str] = None
    hermes_session_id: Optional[str] = None
    agent_identity: Optional[str] = None
    device_id: Optional[str] = None
    platform: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class DeviceRegisterIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    device_id: str
    hostname: str
    platform: Optional[str] = None
    capabilities: Optional[List[str]] = None
    resources: Optional[Dict[str, Any]] = None
    max_concurrent_tasks: int = 1
    available_agents: Optional[List[str]] = None
    tags: Optional[List[str]] = None

class DeviceHeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    current_load: int = 0
    queue_depth: int = 0


class ReviewDecisionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    note: Optional[str] = None
    auto_dispatch: bool = True
    target: Optional[DispatchTarget] = None
    priority: str = "MEDIUM"


class FeedConnectorUpsertIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    category: str = "general"
    endpoint: str
    enabled: bool = True
    health_status: str = "healthy"
    metadata: Optional[Dict[str, Any]] = None


class EvaluationRunIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    suite_name: str
    agent_class: str
    status: str = "completed"
    score: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None


class EvaluationSuiteUpsertIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    agent_class: str
    enabled: bool = True
    cadence: str = "daily"
    threshold: float = 0.8
    description: str = ""
    metadata: Optional[Dict[str, Any]] = None


class WorkflowControlIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str  # drain | resume | set_limits
    max_concurrent_workflows: Optional[int] = None
    max_batch_backlog: Optional[int] = None


class WorkflowReplanIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reason: str
    severity: str = "warning"
    metadata: Optional[Dict[str, Any]] = None


class WorkflowBudgetUpdateIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token_budget: Optional[int] = None
    time_budget_s: Optional[int] = None
    retry_budget: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None

# -----------------------
# Config / Security
# -----------------------
security = HTTPBasic(auto_error=False)
USER = os.getenv("BASIC_AUTH_USER")
PASS = os.getenv("BASIC_AUTH_PASS")
TRUSTED_AUTH_HEADER = os.getenv("TRUSTED_AUTH_HEADER", "").strip()
if not USER and not PASS and not TRUSTED_AUTH_HEADER:
    print("WARNING: No auth configured. Set BASIC_AUTH_USER/BASIC_AUTH_PASS or TRUSTED_AUTH_HEADER.")
    print("WARNING: All auth-required endpoints will return 401.")

API_TOKEN: Optional[str] = os.getenv("API_TOKEN")  # If set, required for /upload-audio
PAPERCLIP_WEBHOOK_SECRET: Optional[str] = os.getenv("PAPERCLIP_WEBHOOK_SECRET")
VOICE_WEBHOOK_SECRET: Optional[str] = os.getenv("VOICE_WEBHOOK_SECRET")
PAPERCLIP_AGENT_ID = os.getenv("PAPERCLIP_AGENT_ID", "Hermes Agent")
WS_AUTH_REQUIRED = os.getenv("WS_AUTH_REQUIRED", "1").strip().lower() not in {"0", "false", "no", "off"}
WS_AUTH_TOKEN = os.getenv("WS_AUTH_TOKEN", API_TOKEN or "")
INTENT_AUTO_DISPATCH_CONFIDENCE = float(os.getenv("INTENT_AUTO_DISPATCH_CONFIDENCE", "0.72"))
INTENT_AUTO_CANCEL_CONFIDENCE = float(os.getenv("INTENT_AUTO_CANCEL_CONFIDENCE", "0.80"))

TRANSCRIPTIONS_ROOT = pathlib.Path(os.getenv("TRANSCRIPTIONS_ROOT", "./transcriptions")).resolve()
TRANSCRIPTIONS_ROOT.mkdir(parents=True, exist_ok=True)
CAPTURES_ROOT = pathlib.Path(os.getenv("CAPTURES_ROOT", "./artifacts/captures")).resolve()
CAPTURES_ROOT.mkdir(parents=True, exist_ok=True)

WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")        # e.g., "cuda", "cpu", "auto"
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE_TYPE", "int8") # e.g., "float16", "int8"
MULTIPART_AVAILABLE = multipart_available()
# -----------------------
# App + Static/Template
# -----------------------
_lifespan_logger = logging.getLogger("uvicorn.error")
_api_logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_configuration(strict=True)
    try:
        neo = Neo4jClient()
        neo.ensure_schema()
    except Exception as e:
        _lifespan_logger.warning(f"Neo4j schema initialization warning at startup: {e}")
    finally:
        try:
            neo.close()
        except Exception:
            pass
    try:
        from .paperclip_poller import schedule_paperclip_poller
        schedule_paperclip_poller()
    except Exception as e:
        _lifespan_logger.warning(f"Paperclip poller not scheduled: {e}")
    try:
        from .intent_orchestrator import schedule_intent_orchestrator
        schedule_intent_orchestrator()
    except Exception as e:
        _lifespan_logger.warning(f"Intent orchestrator not scheduled: {e}")
    try:
        from .maintenance import schedule_maintenance_job
        schedule_maintenance_job()
    except Exception as e:
        _lifespan_logger.warning(f"Maintenance job not scheduled: {e}")
    try:
        from .model_prober import schedule_prober
        schedule_prober()
    except Exception as e:
        _lifespan_logger.warning(f"Model prober not scheduled: {e}")
    try:
        from .maintenance import run_stale_claim_reaper_loop
        run_stale_claim_reaper_loop()
    except Exception as e:
        _lifespan_logger.warning(f"Stale claim reaper not started: {e}")
    try:
        from .fleet_executor import _start_executor_loop
        _start_executor_loop()
    except Exception as e:
        _lifespan_logger.warning(f"Fleet executor not started: {e}")
    try:
        from .kg_harvester import _start_harvester_loop
        _start_harvester_loop()
    except Exception as e:
        _lifespan_logger.warning(f"KG harvester not started: {e}")
    try:
        from .repo_task_generator import start_repo_task_generator
        start_repo_task_generator()
    except Exception as e:
        _lifespan_logger.warning(f"Repo task generator not scheduled: {e}")
    try:
        from .llm.client import start_fleet_loader
        start_fleet_loader()
    except Exception as e:
        _lifespan_logger.warning(f"Fleet loader not started: {e}")
    yield


app = FastAPI(title="AssistX API & UI", lifespan=lifespan)
setup_logging()
install_logging_middleware(app)


def _validation_error_response(exc: RequestValidationError) -> JSONResponse:
    """Return a stable, UI-safe validation error envelope."""
    errors = []
    for item in exc.errors():
        loc = [str(part) for part in item.get("loc", ())]
        field = ".".join(loc[1:]) if len(loc) > 1 and loc[0] in {"body", "query", "path", "header", "cookie"} else ".".join(loc)
        errors.append(
            {
                "field": field or None,
                "code": item.get("type", "validation_error"),
                "message": "Invalid value",
            }
        )
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Request validation failed",
            "error": {
                "code": "http_422",
                "message": "Request validation failed",
                "status_code": 422,
            },
            "errors": errors,
        },
    )


@app.exception_handler(RequestValidationError)
async def _request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return _validation_error_response(exc)


def _http_exception_response(exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        message = detail.get("message") or "Request failed"
    else:
        message = str(detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": detail,
            "error": {
                "code": f"http_{exc.status_code}",
                "message": message,
                "status_code": exc.status_code,
            },
        },
        headers=exc.headers,
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    return _http_exception_response(exc)

# CORS is useful for the ingestion endpoints (web UIs, local tools, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the Phase 2 swarm router with auth dependency
from .swarm_routes import router as swarm_router, set_auth_dependency
app.include_router(swarm_router)

# Mount static & templates like v1
ROOT = pathlib.Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(ROOT / "templates"))
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


RATE_LIMITED_ROUTES = [
    ("POST", "/api/dispatch", DISPATCH_LIMITER),
    ("POST", "/api/paperclip/events", EVENT_LIMITER),
    ("POST", "/api/ask", ASK_LIMITER),
    ("POST", "/api/intents", INTENT_LIMITER),
]

def _rate_limit_key(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host or "unknown"
    return "unknown"

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    for method, path, limiter in RATE_LIMITED_ROUTES:
        if request.method == method and request.url.path == path:
            client_key = _rate_limit_key(request)
            allowed, remaining, retry_after = limiter.check(client_key)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded",
                        "error": {
                            "code": "http_429",
                            "message": "Rate limit exceeded",
                            "status_code": 429,
                        },
                        "retry_after_seconds": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            break
    return await call_next(request)


@app.middleware("http")
async def neo4j_guard(request, call_next):
    try:
        return await call_next(request)
    except ServiceUnavailable:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Neo4j unavailable. In host mode, set NEO4J_URI=bolt://host.docker.internal:7687 and add extra_hosts.",
                "error": {
                    "code": "http_503",
                    "message": "Neo4j unavailable",
                    "status_code": 503,
                },
            },
        )
    except ValueError as e:
        if "Cannot resolve address" in str(e):
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Neo4j hostname not resolvable from container. Use host.docker.internal (with host-gateway) or run neo4j in Compose.",
                    "error": {
                        "code": "http_503",
                        "message": "Neo4j hostname not resolvable",
                        "status_code": 503,
                    },
                },
            )
        raise

@app.middleware("http")
async def request_metrics_middleware(request: Request, call_next):
    response = await call_next(request)
    try:
        REQUESTS.labels(
            path=request.url.path,
            method=request.method,
            status=str(response.status_code),
        ).inc()
    except Exception:
        pass
    return response

@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    start = _time.perf_counter()
    response = await call_next(request)
    elapsed_ms = int((_time.perf_counter() - start) * 1000)
    _api_logger.info(
        "request_complete path=%s method=%s status=%s duration_ms=%s runtime_profile=%s",
        request.url.path,
        request.method,
        response.status_code,
        elapsed_ms,
        runtime_profile(),
    )
    return response

def _auth_user_from_credentials(
    request: Request,
    credentials: HTTPBasicCredentials | None,
) -> Optional[str]:
    if TRUSTED_AUTH_HEADER:
        trusted_user = request.headers.get(TRUSTED_AUTH_HEADER)
        if trusted_user:
            return trusted_user
    if credentials is None:
        return None
    username_ok = hmac.compare_digest(credentials.username, USER)
    password_ok = hmac.compare_digest(credentials.password, PASS)
    if username_ok and password_ok:
        return credentials.username
    return None

def auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    user = _auth_user_from_credentials(request, credentials)
    if user is None:
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    return user


# Inject the auth dependency into swarm routes
set_auth_dependency(auth)


_neo_instance: Optional[Neo4jClient] = None

def _neo() -> Neo4jClient:
    global _neo_instance
    if _neo_instance is None:
        _neo_instance = Neo4jClient()
        _neo_instance.shared = True
    return _neo_instance

_neo_fleet_instance: Optional[Neo4jClient] = None

def _neo_fleet() -> Neo4jClient:
    """Dedicated Neo4j client/pool for high-concurrency fleet executor endpoints
    (agent task listing, claim, complete) so they never starve /health or
    human-facing routes on the shared _neo() pool."""
    global _neo_fleet_instance
    if _neo_fleet_instance is None:
        _neo_fleet_instance = Neo4jClient(
            pool_size=int(os.getenv("NEO4J_FLEET_POOL_SIZE", "200"))
        )
        _neo_fleet_instance.shared = True
    return _neo_fleet_instance

_paperclip_client: Optional[PaperclipClient] = None
_workflow_control_lock = threading.Lock()
_workflow_control_state: Dict[str, Any] = {
    "mode": "resume",  # resume | drain
    "max_concurrent_workflows": 20,
    "max_batch_backlog": 200,
    "updated_at_ts": 0,
}


def _get_workflow_control() -> dict[str, Any]:
    with _workflow_control_lock:
        return dict(_workflow_control_state)


def _set_workflow_control(**kwargs: Any) -> None:
    with _workflow_control_lock:
        _workflow_control_state.update(kwargs)
        _workflow_control_state["updated_at_ts"] = int(_time.time() * 1000)
_sophia_policy_state: Dict[str, Any] = {
    "last_fingerprint": None,
    "last_seen_ts": 0,
}

def get_paperclip_client() -> Optional[PaperclipClient]:
    global _paperclip_client
    if _paperclip_client is not None:
        return _paperclip_client
    try:
        _paperclip_client = PaperclipClient()
        return _paperclip_client
    except ValueError:
        return None

def _verify_paperclip_signature(body: BaseModel, signature: Optional[str]) -> None:
    if not PAPERCLIP_WEBHOOK_SECRET:
        _api_logger.error("PAPERCLIP_WEBHOOK_SECRET not set; refusing unauthenticated Paperclip webhook")
        raise HTTPException(status_code=503, detail="Paperclip webhook secret not configured")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing Paperclip signature header (X-Paperclip-Signature)")
    payload = body.model_dump_json(exclude_none=True).encode("utf-8")
    expected = hmac.new(PAPERCLIP_WEBHOOK_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    accepted = {expected, f"sha256={expected}"}
    if not any(hmac.compare_digest(signature, candidate) for candidate in accepted):
        raise HTTPException(status_code=401, detail="Invalid Paperclip signature")

def _verify_voice_signature(body: BaseModel, signature: Optional[str]) -> None:
    if not VOICE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Voice webhook secret not configured")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing voice signature header (X-Voice-Signature)")
    payload = body.model_dump_json(exclude_none=True).encode("utf-8")
    expected = hmac.new(VOICE_WEBHOOK_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    accepted = {expected, f"sha256={expected}"}
    if not any(hmac.compare_digest(signature, candidate) for candidate in accepted):
        raise HTTPException(status_code=401, detail="Invalid voice signature")

def _require_ws_auth(token: Optional[str]) -> None:
    if not WS_AUTH_REQUIRED:
        return
    if not WS_AUTH_TOKEN:
        raise HTTPException(status_code=503, detail="WebSocket auth token not configured")
    if not token or not hmac.compare_digest(token, WS_AUTH_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")

def _cancel_tasks_for_intent(neo: Neo4jClient, intent_id: str, reason: str) -> int:
    with neo._session() as s:
        rec = s.run(
            """
            MATCH (i:Intent {id:$intent_id})-[:CREATED_TASK]->(t:Task)
            WHERE t.status IN ['READY','CLAIMED','RUNNING']
            SET t.status='CANCELLED',
                t.cancelled_reason=$reason,
                t.updated_at=datetime(),
                t.updated_at_ts=timestamp()
            RETURN count(t) AS cancelled
            """,
            {"intent_id": intent_id, "reason": reason[:500]},
        ).single()
    return int(rec["cancelled"] if rec else 0)


def _intent_outcome_and_confidence(text: str, classification: str) -> tuple[str, float]:
    text_l = (text or "").strip().lower()
    words = len(text_l.split())
    questionish = "?" in text_l or text_l.startswith(("what", "who", "where", "when", "why", "how"))

    if classification == CLASSIFICATION_CANCEL:
        direct_cancel = any(k in text_l for k in ("cancel", "stop", "never mind", "scratch that"))
        return "cancellation", 0.94 if direct_cancel else 0.85
    if classification == CLASSIFICATION_MEMORY:
        explicit_memory = any(k in text_l for k in ("remember", "note", "for the record", "keep in mind"))
        return "memory_capture", 0.86 if explicit_memory else 0.72
    if classification == CLASSIFICATION_QUERY:
        return "information_query", 0.90 if questionish else 0.75
    if classification == CLASSIFICATION_TASK:
        if words <= 2:
            return "ambiguous", 0.42
        direct_request = any(
            k in text_l
            for k in ("please", "can you", "could you", "i need you", "create", "build", "fix", "update")
        )
        return "actionable_task", 0.83 if direct_request else 0.70
    return "ambiguous", 0.35


def _intent_policy_action(outcome: str, confidence: float) -> str:
    if outcome == "cancellation":
        return "auto_cancel_eligible" if confidence >= INTENT_AUTO_CANCEL_CONFIDENCE else "review_cancel"
    if outcome == "actionable_task":
        return "auto_dispatch_eligible" if confidence >= INTENT_AUTO_DISPATCH_CONFIDENCE else "review_dispatch"
    if outcome in {"memory_capture", "information_query"}:
        return "no_dispatch"
    return "needs_clarification"


def _json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}

def _normalize_ask_question(question: str) -> str:
    """
    Normalize user questions before they are logged and sent into the QA pipeline.

    This keeps accidental leading/trailing whitespace and control characters from
    leaking into Neo4j titles, cache keys, and downstream prompts.
    """
    q = (question or "").replace("\x00", "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question must not be empty")
    return q


def _is_claim_allowed_for_workflow_control(task: Dict[str, Any]) -> tuple[bool, str]:
    mode = str(_get_workflow_control().get("mode") or "resume")
    if mode != "drain":
        return True, ""
    payload = _json_dict(task.get("payload_json"))
    queue_class = str(payload.get("queue_class") or task.get("queue_class") or "interactive")
    # During drain mode, only critical queue-class tasks can be newly claimed.
    if queue_class == "critical":
        return True, ""
    return False, f"workflow control is in drain mode; queue_class={queue_class} is paused"


def _queue_class_for_task(task: Dict[str, Any]) -> str:
    payload = _json_dict(task.get("payload_json"))
    qclass = str(payload.get("queue_class") or task.get("queue_class") or "interactive")
    if qclass not in {"interactive", "batch", "critical"}:
        return "interactive"
    return qclass


def _workflow_runtime_snapshot(neo: Neo4jClient) -> Dict[str, int]:
    with neo._session() as s:
        rec = s.run(
            """
            MATCH (t:Task)
            WHERE t.status IN ['READY','CLAIMED','RUNNING']
            RETURN
              sum(CASE WHEN t.status='RUNNING' THEN 1 ELSE 0 END) AS running
            """
        ).single()
        running = int(rec["running"] if rec and rec["running"] is not None else 0)
    return {"running": running, "batch_ready_direct": 0}


def _maybe_dead_letter_exhausted_task(
    neo: Neo4jClient,
    task_id: str,
    completed_status: str,
    actor: str,
) -> Optional[str]:
    if completed_status not in {"FAILED", "CANCELLED"}:
        return None
    try:
        with neo._session() as s:
            labels = {row["label"] for row in s.run("CALL db.labels() YIELD label RETURN label")}
            if "WorkflowBudget" not in labels:
                return None
            rec = s.run(
                """
                MATCH (t:Task {id:$task_id})
                OPTIONAL MATCH p=(t)-[:PART_OF|HAS_CHILD*0..4]-(wf:Task)-[:HAS_BUDGET]->(b:WorkflowBudget)
                WITH t, wf, b,
                     CASE WHEN p IS NULL THEN 999 ELSE length(p) END AS depth
                ORDER BY depth ASC
                WITH t, wf, b
                LIMIT 1
                RETURN
                    t.id AS task_id,
                    coalesce(t.failure_count, 0) AS failure_count,
                    coalesce(t.dead_lettered, false) AS dead_lettered,
                    b.retry_budget AS retry_budget,
                    coalesce(wf.id, t.id) AS workflow_id
                """,
                {"task_id": task_id},
            ).single()
            if not rec:
                return None
            retry_budget = rec["retry_budget"]
            if retry_budget is None:
                return None
            failure_count = int(rec["failure_count"] if rec["failure_count"] is not None else 0)
            if failure_count <= int(retry_budget):
                return None
            if bool(rec["dead_lettered"]):
                return None
            workflow_id = str(rec["workflow_id"] or task_id)
            detail = (
                f"Task {task_id} exceeded retry budget: "
                f"failure_count={failure_count}, retry_budget={int(retry_budget)}"
            )
            incident_id = neo.create_workflow_incident(
                workflow_id=workflow_id,
                incident_type="retry_budget_exhausted",
                severity="warning",
                detail=detail,
                metadata={
                    "task_id": task_id,
                    "failure_count": failure_count,
                    "retry_budget": int(retry_budget),
                    "completed_status": completed_status,
                    "dead_lettered_by": actor,
                },
            )
            s.run(
                """
                MATCH (t:Task {id:$task_id})
                SET t.status='REVIEW',
                    t.dead_lettered=true,
                    t.dead_letter_reason='retry_budget_exhausted',
                    t.dead_letter_incident_id=$incident_id,
                    t.dead_lettered_by=$actor,
                    t.dead_lettered_at=datetime(),
                    t.dead_lettered_at_ts=timestamp(),
                    t.updated_at=datetime(),
                    t.updated_at_ts=timestamp()
                """,
                {"task_id": task_id, "incident_id": incident_id, "actor": actor},
            ).consume()
            return incident_id
    except Exception:
        return None


def _apply_workflow_admission(tasks: List[Dict[str, Any]], runtime: Dict[str, int]) -> List[Dict[str, Any]]:
    control = _get_workflow_control()
    mode = str(control.get("mode") or "resume")
    max_running = int(control.get("max_concurrent_workflows") or 20)
    max_batch_backlog = int(control.get("max_batch_backlog") or 200)
    running = int(runtime.get("running") or 0)
    batch_ready = int(runtime.get("batch_ready_direct") or 0)

    filtered: List[Dict[str, Any]] = []
    for task in tasks:
        qclass = _queue_class_for_task(task)
        if mode == "drain" and qclass != "critical":
            continue
        if running >= max_running and qclass != "critical":
            continue
        if batch_ready > max_batch_backlog and qclass == "batch":
            continue
        task["queue_class"] = qclass
        filtered.append(task)
    return filtered


def _sophia_queue_class(event_type: str, auth_state: Optional[str]) -> str:
    et = (event_type or "").strip().lower()
    st = (auth_state or "").strip().lower()
    policy = _sophia_routing_policy()
    by_auth = policy.get("by_auth_state", {})
    by_event_prefix = policy.get("by_event_type_prefix", {})
    default_class = str(policy.get("default_queue_class", "interactive"))
    if st and st in by_auth:
        return str(by_auth[st])
    for prefix, qclass in by_event_prefix.items():
        if et.startswith(prefix):
            return str(qclass)
    return default_class


def _sophia_routing_policy() -> Dict[str, Any]:
    # Optional override via JSON env var:
    # ASSISTX_SOPHIA_ROUTING_POLICY='{"default_queue_class":"interactive","by_auth_state":{"unknown_unverified":"critical"},"by_event_type_prefix":{"meeting":"batch"}}'
    raw = os.getenv("ASSISTX_SOPHIA_ROUTING_POLICY", "").strip()
    default = {
        "default_queue_class": "interactive",
        "by_auth_state": {
            "not_scott_known": "critical",
            "unknown_unverified": "critical",
        },
        "by_event_type_prefix": {
            "meeting": "batch",
            "batch": "batch",
        },
    }
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return default
        out = dict(default)
        if isinstance(parsed.get("default_queue_class"), str):
            out["default_queue_class"] = parsed["default_queue_class"]
        if isinstance(parsed.get("by_auth_state"), dict):
            out["by_auth_state"] = {str(k): str(v) for k, v in parsed["by_auth_state"].items()}
        if isinstance(parsed.get("by_event_type_prefix"), dict):
            out["by_event_type_prefix"] = {str(k): str(v) for k, v in parsed["by_event_type_prefix"].items()}
        return out
    except Exception:
        return default


def _sophia_routing_policy_fingerprint(policy: Optional[Dict[str, Any]] = None) -> str:
    obj = policy or _sophia_routing_policy()
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _record_sophia_policy_change_if_needed(
    neo: Neo4jClient,
    user: str,
    policy: Dict[str, Any],
    source: str,
) -> Optional[str]:
    fingerprint = _sophia_routing_policy_fingerprint(policy)
    prev = _sophia_policy_state.get("last_fingerprint")
    _sophia_policy_state["last_seen_ts"] = int(_time.time() * 1000)
    if prev == fingerprint:
        return None
    incident_id = neo.create_workflow_incident(
        workflow_id="sophia-policy",
        incident_type="routing_policy_changed",
        severity="info",
        detail=f"Sophia routing policy fingerprint changed: {prev or 'none'} -> {fingerprint}",
        metadata={
            "source": source,
            "updated_by": user,
            "previous_fingerprint": prev,
            "new_fingerprint": fingerprint,
            "policy": policy,
        },
    )
    _sophia_policy_state["last_fingerprint"] = fingerprint
    return incident_id


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
    model_config = ConfigDict(extra="ignore")

    prompt: Optional[str] = None                          # one-shot prompt
    messages: Optional[List[Dict[str, str]]] = None       # chat format: [{"role":"user","content":"..."}]
    model: Optional[str] = None                           # defaults to OLLAMA_MODEL env
    options: Optional[Dict[str, Any]] = None              # temperature, top_p, etc.
    system: Optional[str] = Field(default=None, description="Optional system instruction")


# =======================
# UI / Orchestration (v1)
# =======================
def _auto_router_base_url() -> str:
    return os.getenv("AUTO_ROUTER_BASE_URL", "").strip().rstrip("/")


def _fetch_json(url: str, *, timeout: float = 6.0) -> dict[str, Any]:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    try:
        payload = resp.json()
    except Exception as exc:
        raise ValueError(f"non-JSON response from {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object from {url}")
    return payload


def _fetch_auto_router_fleet_report() -> dict[str, Any]:
    base_url = _auto_router_base_url()
    if not base_url:
        return {
            "ok": False,
            "configured": False,
            "source_url": None,
            "error": "AUTO_ROUTER_BASE_URL is not configured",
            "report": {},
            "summary": {},
            "loadouts": [],
            "task_profiles": [],
        }

    source_url = f"{base_url}/admin/ops/summary"
    try:
        summary = _fetch_json(source_url)
    except Exception as exc:
        return {
            "ok": False,
            "configured": True,
            "source_url": source_url,
            "error": str(exc)[:500],
            "report": {},
            "summary": {},
            "loadouts": [],
            "task_profiles": [],
        }

    report = summary.get("fleet_loadout_report") or {}
    return {
        "ok": bool(report),
        "configured": True,
        "source_url": source_url,
        "captured_at": report.get("captured_at"),
        "report": report,
        "summary": summary.get("fleet_dispatcher_stats") or {},
        "loadouts": report.get("loadouts") or [],
        "task_profiles": report.get("task_profiles") or [],
        "snapshot_summary": report.get("snapshot_summary") or report.get("summary") or {},
    }


@app.get("/", response_class=RedirectResponse)
def home():
    return RedirectResponse(url="/live")

# ---------- Phase 4 Command Center UI ----------

@app.get("/command-center", response_class=HTMLResponse)
def command_center(request: Request, user: str = Depends(auth)):
    return templates.TemplateResponse("command_center.html", {"request": request})

@app.get("/fleet", response_class=HTMLResponse)
def fleet_ui(request: Request, user: str = Depends(auth)):
    # DEPRECATED: fleet/router ownership moves to auto-router.
    _api_logger.warning("DEPRECATED route /fleet accessed — fleet/router ownership moves to auto-router")
    return templates.TemplateResponse("fleet.html", {"request": request})

@app.get("/fleet-dashboard", response_class=HTMLResponse)
def fleet_dashboard_ui(request: Request, user: str = Depends(auth)):
    """New comprehensive fleet dashboard with live node/model/task visualization."""
    return templates.TemplateResponse("fleet_dashboard.html", {"request": request})

@app.get("/routing", response_class=HTMLResponse)
def routing_ui(request: Request, user: str = Depends(auth)):
    # DEPRECATED: fleet/router ownership moves to auto-router.
    _api_logger.warning("DEPRECATED route /routing accessed — fleet/router ownership moves to auto-router")
    return templates.TemplateResponse("routing.html", {"request": request})


@app.get("/intents", response_class=HTMLResponse)
def intents_ui(request: Request, user: str = Depends(auth)):
    return templates.TemplateResponse("intents.html", {"request": request})

@app.get("/dispatches", response_class=HTMLResponse)
def dispatches_ui(request: Request, user: str = Depends(auth)):
    return templates.TemplateResponse("dispatches.html", {"request": request})

@app.get("/sessions", response_class=HTMLResponse)
def sessions_ui(request: Request, user: str = Depends(auth)):
    return templates.TemplateResponse("sessions.html", {"request": request})

@app.get("/memory", response_class=HTMLResponse)
def memory_ui(request: Request, user: str = Depends(auth)):
    return templates.TemplateResponse("memory.html", {"request": request})

@app.get("/devices", response_class=HTMLResponse)
def devices_ui(request: Request, user: str = Depends(auth)):
    return templates.TemplateResponse("devices.html", {"request": request})

@app.get("/review", response_class=HTMLResponse)
def review_ui(request: Request, user: str = Depends(auth)):
    return templates.TemplateResponse("review_queue.html", {"request": request})

# ------------------------------------------------

@app.get("/tasks/review", response_class=HTMLResponse)
def tasks_review(request: Request, limit: int = 50, user: str = Depends(auth)):
    neo = _neo()
    with neo._session() as s:
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
    with neo._session() as s:
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
    with neo._session() as s:
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
    with neo._session() as s:
        res = s.run("MATCH (r:AgentRun) RETURN r ORDER BY r.started_at DESC LIMIT $limit", {"limit": limit})
        rows = [dict(r[0]) for r in res]
    neo.close()
    return templates.TemplateResponse("runs.html", {"request": request, "runs": rows})


@app.get("/api/fleet/loadouts")
def api_fleet_loadouts(user: str = Depends(auth)):
    payload = _fetch_auto_router_fleet_report()
    return JSONResponse(payload, status_code=200 if payload.get("ok") else 503)

@app.get("/metrics", response_class=HTMLResponse)
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
    payload = build_runtime_health()
    status_code = 200 if payload.get("ok") else 503
    return JSONResponse(status_code=status_code, content=payload)

@app.get("/api/context/projection")
def api_context_projection():
    neo = _neo()
    try:
        return neo.export_context_projection()
    finally:
        neo.close()

@app.get("/fleet/status")
def api_fleet_status(
    window_minutes: int = Query(30, ge=1, le=24 * 60),
    user: str = Depends(auth),
):
    """Single-call live view of fleet work: task counts by status, online
    nodes, and recent completions/failures. For operator dashboards."""
    neo = _neo()
    try:
        return neo.fleet_status(window_minutes=window_minutes)
    finally:
        neo.close()


# =======================
# Fleet Dashboard API
# =======================

_fleet_executor_instance: Optional[Any] = None

def _get_fleet_executor() -> Any:
    """Get or create the fleet executor instance to access its state."""
    global _fleet_executor_instance
    if _fleet_executor_instance is None:
        from .fleet_executor import FleetExecutor
        _fleet_executor_instance = FleetExecutor()
        _fleet_executor_instance._refresh_nodes()
    return _fleet_executor_instance

def _get_fleet_routing() -> Any:
    """Get the fleet routing singleton."""
    from .fleet_executor import _get_routing
    return _get_routing()


@app.get("/api/fleet/dashboard")
def api_fleet_dashboard(user: str = Depends(auth)):
    """Comprehensive fleet dashboard: nodes, models, tasks, performance."""
    executor = _get_fleet_executor()
    routing = _get_fleet_routing()
    
    nodes = []
    for n in executor._nodes:
        hn = n.get("hostname", n.get("ip", "?"))
        inflight = executor._node_inflight.get(hn, 0)
        latency = executor._node_latency.get(hn, 0)
        pick_count = executor._pick_count.get(hn, 0)
        weight = n.get("weight", 1)
        sem = executor._node_semaphores.get(hn)
        sem_available = sem._value if sem else 0
        
        # Get benchmark performance for models on this node
        model_perf = {}
        for model in n.get("loaded_models", []):
            perf = routing.get_model_perf(hn, model)
            if perf:
                model_perf[model] = {
                    "tps_med": perf.get("tps_med", 0),
                    "eval_score": perf.get("eval_score", 0),
                    "composite": perf.get("composite_score", 0),
                    "ttft_med": perf.get("ttft_med", 0),
                    "load_s": perf.get("load_s", 0),
                    "ok": perf.get("ok", True),
                    "concurrency_tier": perf.get("concurrency_tier", 1),
                }
        
        # Get hardware specs
        specs = routing._node_specs.get(hn, {})
        
        nodes.append({
            "hostname": hn,
            "ip": n.get("ip", ""),
            "weight": weight,
            "capabilities": n.get("capabilities", []),
            "loaded_models": n.get("loaded_models", []),
            "model_perf": model_perf,
            "hardware": {
                "ram_gib": specs.get("ram_gib"),
                "vram_gib": specs.get("vram_gib"),
                "cpu": specs.get("cpu"),
            },
            "health": {
                "last_seen": n.get("last_seen"),
                "lmstudio_ok": n.get("lmstudio_ok", False),
                "inflight_tasks": inflight,
                "max_concurrent": weight,
                "latency_ema_s": round(latency, 2),
                "pick_count": pick_count,
                "semaphore_available": sem_available,
            },
            "routing": {
                "best_models": routing._node_models.get(hn.lower(), []),
            },
        })
    
    # Task distribution by node
    task_distribution = {}
    for hn, count in executor._node_inflight.items():
        task_distribution[hn] = count
    
    # Overall stats
    total_inflight = sum(executor._node_inflight.values())
    total_weight = sum(n.get("weight", 1) for n in executor._nodes)
    healthy_nodes = sum(1 for n in executor._nodes if n.get("lmstudio_ok", False))
    
    return {
        "timestamp": _now_ts(),
        "summary": {
            "total_nodes": len(executor._nodes),
            "healthy_nodes": healthy_nodes,
            "total_weight": total_weight,
            "total_inflight": total_inflight,
            "llm_semaphore_available": executor._llm_sem._value,
            "script_semaphore_available": executor._script_sem._value,
        },
        "nodes": nodes,
        "task_distribution": task_distribution,
        "routing": {
            "model_to_best_node": routing._model_to_node,
            "model_routing": routing._routing,
        },
    }


@app.get("/api/fleet/nodes")
def api_fleet_nodes(user: str = Depends(auth)):
    """List all fleet nodes with live state."""
    executor = _get_fleet_executor()
    return {
        "nodes": [
            {
                "hostname": n.get("hostname", n.get("ip", "?")),
                "ip": n.get("ip", ""),
                "weight": n.get("weight", 1),
                "capabilities": n.get("capabilities", []),
                "loaded_models": n.get("loaded_models", []),
                "lmstudio_ok": n.get("lmstudio_ok", False),
                "last_seen": n.get("last_seen"),
                "inflight": executor._node_inflight.get(n.get("hostname", n.get("ip", "")), 0),
            }
            for n in executor._nodes
        ]
    }


@app.get("/api/fleet/models")
def api_fleet_models(user: str = Depends(auth)):
    """Model inventory across all nodes with benchmark performance."""
    executor = _get_fleet_executor()
    routing = _get_fleet_routing()
    
    models = {}
    for n in executor._nodes:
        hn = n.get("hostname", n.get("ip", "?"))
        for model in n.get("loaded_models", []):
            if model not in models:
                models[model] = {"nodes": [], "best_node": None, "best_composite": 0}
            
            perf = routing.get_model_perf(hn, model)
            composite = perf.get("composite_score", 0) if perf else 0
            
            models[model]["nodes"].append({
                "hostname": hn,
                "tps_med": perf.get("tps_med", 0) if perf else 0,
                "eval_score": perf.get("eval_score", 0) if perf else 0,
                "composite": composite,
                "ok": perf.get("ok", True) if perf else False,
            })
            
            if composite > models[model]["best_composite"]:
                models[model]["best_composite"] = composite
                models[model]["best_node"] = hn
    
    return {"models": models}


@app.post("/api/fleet/refresh-models")
def api_fleet_refresh_models(user: str = Depends(auth)):
    """Force re-probe of all fleet nodes for hot models. Invalidates the
    60s cache so the LLM client picks up newly loaded models immediately."""
    try:
        from assistx.llm import client as llm_client
        llm_client._fleet_inventory_at = 0.0
        llm_client._fleet_inventory.clear()
        inv = llm_client._fleet_model_inventory()
        return {"ok": True, "hot_models": len(inv), "models": list(inv.keys())}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/fleet/loader/status")
def api_fleet_loader_status(user: str = Depends(auth)):
    """Return the autonomous loader's current state: discovered models, per-node
    plan (budget, hot models, planned models, actions), and learned ModelPerf
    scores from the knowledge graph."""
    try:
        from assistx.llm import client as llm_client
        state = llm_client.get_loader_state()
        # Pull learned ModelPerf scores from the KG for context.
        perf = {}
        try:
            neo = _neo()
            with neo._session() as s:
                res = s.run("""
                    MATCH (p:ModelPerf)
                    RETURN p.model AS model, avg(p.quality_score) AS q,
                           avg(p.latency_ms) AS lat, avg(p.tps) AS tps,
                           count(p) AS runs
                """)
                for row in res:
                    perf[row["model"]] = {
                        "quality": round(row.get("q") or 0.0, 3),
                        "latency_ms": round(row.get("lat") or 0.0, 1),
                        "tps": round(row.get("tps") or 0.0, 1),
                        "runs": row.get("runs") or 0,
                    }
        except Exception:
            pass
        from assistx.llm import client as _lc
        return {
            "running": state.get("running", False),
            "cycle": state.get("cycle", 0),
            "last_action": state.get("last_action", ""),
            "last_run_ts": state.get("last_run_ts", 0),
            "discovered_models": state.get("discovered_models", []),
            "owners": state.get("owners", {}),
            "pinned": sorted(_lc._loader_pinned_models),
            "demand": sorted(_lc._loader_demand),
            "node_configs": {f"{b}|{m}": c
                             for (b, m), c in _lc._loader_node_configs.items()},
            "nodes": list(_lc._fleet_nodes),
            "per_node": state.get("per_node", {}),
            "learned_perf": perf,
            "hot_inventory": {},
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/fleet/loader/wishlist")
def api_fleet_loader_wishlist(payload: Dict[str, Any], user: str = Depends(auth)):
    """Operator pins the loader's wishlist to a specific set of model ids (or
    empty list to return control to the auto heuristic).  The operator decides
    what loads — not the code."""
    from assistx.llm import client as llm_client
    models = payload.get("models", []) or []
    llm_client.set_loader_wishlist(models)
    return {"ok": True, "pinned": sorted(llm_client._loader_pinned_models)}


@app.post("/api/fleet/loader/load")
def api_fleet_loader_load(payload: Dict[str, Any], user: str = Depends(auth)):
    """Operator loads a specific model on a specific node now and waits for hot."""
    from assistx.llm import client as llm_client
    base = payload.get("base_url")
    mid = payload.get("model")
    if not base or not mid:
        raise HTTPException(status_code=400, detail="base_url and model required")
    return llm_client.load_model_on_node(base, mid)


@app.post("/api/fleet/loader/unload")
def api_fleet_loader_unload(payload: Dict[str, Any], user: str = Depends(auth)):
    """Operator unloads a specific model from a specific node."""
    from assistx.llm import client as llm_client
    base = payload.get("base_url")
    mid = payload.get("model")
    if not base or not mid:
        raise HTTPException(status_code=400, detail="base_url and model required")
    return llm_client.unload_model_on_node(base, mid)


@app.post("/api/fleet/loader/config")
def api_fleet_loader_config(payload: Dict[str, Any], user: str = Depends(auth)):
    """Set (or clear with config=null) the per-node load config for a model:
    context_length, speculative_draft_mtp, speculative_draft_model, parallel, ….
    Applied on the next load of that model on that node."""
    from assistx.llm import client as llm_client
    base = payload.get("base_url")
    mid = payload.get("model")
    cfg = payload.get("config", None)
    if not base or not mid:
        raise HTTPException(status_code=400, detail="base_url and model required")
    llm_client.set_node_config(base, mid, cfg)
    return {"ok": True, "config": llm_client._loader_node_configs.get((base, mid))}


@app.post("/api/fleet/loader/demand")
def api_fleet_loader_demand(payload: Dict[str, Any], user: str = Depends(auth)):
    """Request (or release) that a model be kept resident on the fleet — used by
    subsystems like the portfolio trader.  Pass release=true to drop the request."""
    from assistx.llm import client as llm_client
    mid = payload.get("model")
    if not mid:
        raise HTTPException(status_code=400, detail="model required")
    if payload.get("release"):
        llm_client.release_model(mid)
        return {"ok": True, "demand": sorted(llm_client._loader_demand), "action": "released"}
    llm_client.request_model(mid)
    return {"ok": True, "demand": sorted(llm_client._loader_demand), "action": "requested"}


@app.get("/api/fleet/tasks")
def api_fleet_tasks(
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    user: str = Depends(auth),
):
    """Get tasks with node assignment info."""
    neo = _neo()
    try:
        with neo._session() as s:
            where = ""
            params = {"limit": limit}
            if status:
                where = "WHERE t.status = $status"
                params["status"] = status
            
            query = f"""
            MATCH (t:Task)
            {where}
            OPTIONAL MATCH (t)-[:DISPATCHED_AS]->(d:Dispatch)
            RETURN t, d.node_id as assigned_node
            ORDER BY t.created_at_ts DESC
            LIMIT $limit
            """
            res = s.run(query, params)
            tasks = []
            for row in res:
                t = dict(row["t"])
                t["assigned_node"] = row["assigned_node"]
                tasks.append(t)
            return {"tasks": tasks, "count": len(tasks)}
    finally:
        neo.close()


@app.get("/api/fleet/benchmarks")
def api_fleet_benchmarks(user: str = Depends(auth)):
    """Get benchmark data from fleet_state.json."""
    routing = _get_fleet_routing()
    return {
        "nodes": routing._state.get("nodes", []),
        "loadout": routing._loadout.get("routing", {}),
        "timestamp": routing._state.get("timestamp"),
    }


def _now_ts() -> int:
    return int(_time.time() * 1000)


def _live_probe_node(url: str, timeout: float = 5.0) -> Optional[dict]:
    """Probe a node: list all models on device, then check per-model loadedness in parallel."""
    base = url.rstrip("/")
    if "/v1" not in base:
        base = base + "/v1"
    t0 = _time.time()
    try:
        r = requests.get(f"{base}/models", timeout=timeout)
        ms = int((_time.time() - t0) * 1000)
        if r.status_code != 200:
            return {"online": False, "response_ms": ms, "error": f"HTTP {r.status_code}"}
        data = r.json()
        model_ids = []
        for m in data.get("data", []):
            mid = m.get("id") or m.get("name") or m.get("model")
            if mid:
                model_ids.append(mid)
        remaining = max(1.0, timeout - (_time.time() - t0))
        if model_ids:
            loaded_set = set()
            import concurrent.futures as cf
            per_model_timeout = max(0.5, remaining / max(len(model_ids), 1))
            with cf.ThreadPoolExecutor(max_workers=min(len(model_ids), 12)) as pool:
                def _check_loaded(mid):
                    try:
                        t1 = _time.time()
                        rr = requests.post(f"{base}/chat/completions", json={
                            "model": mid, "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1, "temperature": 0, "stream": False,
                        }, timeout=per_model_timeout)
                        elapsed = int((_time.time() - t1) * 1000)
                        if rr.status_code != 200 or elapsed >= 1500:
                            return mid, False, elapsed
                        body = rr.json()
                        if not isinstance(body, dict):
                            return mid, False, elapsed
                        responded_model = body.get("model", "")
                        if responded_model and mid not in responded_model:
                            return mid, False, elapsed
                        choices = body.get("choices", [])
                        if not choices or not isinstance(choices, list):
                            return mid, False, elapsed
                        msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
                        if not msg.get("content"):
                            return mid, False, elapsed
                        return mid, True, elapsed
                    except (requests.RequestException, json.JSONDecodeError, LookupError, TypeError):
                        return mid, False, None
                for mid, is_loaded, pms in pool.map(_check_loaded, model_ids):
                    if is_loaded:
                        loaded_set.add(mid)
            ms = int((_time.time() - t0) * 1000)
            return {
                "online": True, "response_ms": ms,
                "models": model_ids, "model_count": len(model_ids),
                "loaded_models": list(loaded_set), "loaded_count": len(loaded_set),
            }
        return {"online": True, "response_ms": ms, "models": [], "model_count": 0,
                "loaded_models": [], "loaded_count": 0}
    except requests.RequestException as e:
        ms = int((_time.time() - t0) * 1000)
        return {"online": False, "response_ms": ms, "error": str(e)[:120]}


def _probe_nodes_parallel(nodes: List[dict]) -> Dict[str, dict]:
    """Probe all fleet node URLs in parallel, return dict keyed by base_url."""
    out: Dict[str, dict] = {}
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=min(len(nodes) or 1, 16)) as pool:
        fut_map = {pool.submit(_live_probe_node, n["base_url"]): n["base_url"] for n in nodes}
        for fut in cf.as_completed(fut_map, timeout=15):
            url = fut_map[fut]
            try:
                out[url] = fut.result()
            except Exception as exc:
                out[url] = {"online": False, "response_ms": None, "error": str(exc)[:120]}
    return out


@app.get("/api/live/dashboard")
def api_live_dashboard(user: str = Depends(auth)):
    """Aggregated live dashboard data with live-probed fleet nodes."""
    now = _now_ts()
    five_min_ago = now - 300_000
    one_hour_ago = now - 3_600_000

    neo = _neo()
    fleet_nodes: List[dict] = []
    task_counts: dict = {}
    recent_runs: dict = {}
    runs_last_hour: dict = {}
    label_counts: list = []
    recent_events: list = []
    queue_depth = 0

    # --- fleet nodes ---
    try:
        with neo._session() as s:
            res = s.run("""
                MATCH (m:ModelEndpoint)
                RETURN m.node_id AS node_id, m.base_url AS base_url,
                       m.status AS status, m.models_json AS models_json,
                       m.network_preference AS network_pref,
                       m.purpose AS purpose
                ORDER BY m.node_id
            """)
            for row in res:
                node_id = row.get("node_id") or ""
                base_url = row.get("base_url") or ""
                models_raw = row.get("models_json") or "[]"
                models_list: list = []
                try:
                    models_list = json.loads(models_raw) if isinstance(models_raw, str) else (models_raw or [])
                except (json.JSONDecodeError, TypeError):
                    models_list = []
                fleet_nodes.append({
                    "node_id": node_id,
                    "base_url": base_url,
                    "models": models_list,
                    "network_pref": row.get("network_pref") or "",
                    "purpose": row.get("purpose") or "",
                    "model_count": len(models_list),
                })
    except Exception as exc:
        _api_logger.warning("live fleet query: %s", exc)

    # --- live-probe each fleet node (3s per-node timeout, parallel) ---
    live_results = _probe_nodes_parallel(fleet_nodes)

    for n in fleet_nodes:
        probe = live_results.get(n["base_url"], {})
        n["status"] = "online" if probe.get("online") else "offline"
        n["response_ms"] = probe.get("response_ms")
        loaded_set = set(probe.get("loaded_models", []))
        if probe.get("models"):
            n["models"] = [{"served_name": m, "loaded": m in loaded_set} for m in probe.get("models", [])]
        else:
            cached = n.get("models", [])
            tagged = []
            for m in cached:
                if isinstance(m, str):
                    tagged.append({"served_name": m, "loaded": False})
                elif isinstance(m, dict):
                    tagged.append(dict(m, loaded=False))
                else:
                    tagged.append({"served_name": str(m), "loaded": False})
            n["models"] = tagged
        n["model_count"] = len(n["models"])
        n["loaded_count"] = probe.get("loaded_count", 0)

    # --- task counts ---
    try:
        with neo._session() as s:
            res = s.run("MATCH (t:Task) RETURN t.status AS status, count(t) AS cnt ORDER BY cnt DESC")
            for row in res:
                task_counts[row["status"] or "UNKNOWN"] = row["cnt"]
    except Exception as exc:
        _api_logger.warning("live task query: %s", exc)

    # --- recent runs (5m) ---
    try:
        with neo._session() as s:
            res = s.run("""
                MATCH (r:AgentRun)
                WHERE r.created_at_ts > $five_min_ago
                RETURN r.status AS status, count(r) AS cnt
            """, five_min_ago=five_min_ago)
            for row in res:
                recent_runs[row["status"] or "UNKNOWN"] = row["cnt"]
    except Exception as exc:
        _api_logger.warning("live recent runs: %s", exc)

    # --- runs breakdown (last 1h) ---
    try:
        with neo._session() as s:
            res = s.run("""
                MATCH (r:AgentRun)
                WHERE r.created_at_ts > $one_hour_ago
                RETURN r.status AS status, r.model AS model, count(r) AS cnt
                ORDER BY cnt DESC LIMIT 30
            """, one_hour_ago=one_hour_ago)
            for row in res:
                st = row["status"] or "UNKNOWN"
                mdl = row["model"] or "?"
                key = f"{st}/{mdl}"
                runs_last_hour[key] = {"status": st, "model": mdl, "cnt": row["cnt"]}
    except Exception as exc:
        _api_logger.warning("live runs detail: %s", exc)

    # --- Neo4j label counts ---
    try:
        with neo._session() as s:
            res = s.run("CALL db.labels() YIELD label RETURN label ORDER BY label")
            labels = [row["label"] for row in res]
            for label in labels:
                cres = s.run(f"MATCH (n:{label}) RETURN count(n) AS cnt")
                cnt = cres.single()["cnt"] if cres else 0
                if cnt > 0:
                    label_counts.append({"label": label, "count": cnt})
            label_counts.sort(key=lambda x: -x["count"])
    except Exception as exc:
        _api_logger.warning("live label counts: %s", exc)

    # --- recent trace events (last 5 min) ---
    try:
        with neo._session() as s:
            res = s.run("""
                MATCH (r:AgentRun)
                WHERE r.created_at_ts > $five_min_ago
                RETURN r.id AS run_id, r.status AS status,
                       r.model AS model, r.task_id AS task_id,
                       r.agent AS agent,
                       r.started_at_ts AS started_at_ts,
                       r.ended_at_ts AS ended_at_ts,
                       r.created_at_ts AS created_at_ts,
                       substring(coalesce(r.result_json, '{}'), 0, 120) AS result_preview
                ORDER BY r.created_at_ts DESC
                LIMIT 100
            """, five_min_ago=five_min_ago)
            for row in res:
                recent_events.append({
                    "run_id": row.get("run_id"),
                    "task_id": row.get("task_id"),
                    "status": row.get("status") or "UNKNOWN",
                    "model": row.get("model") or "?",
                    "agent": row.get("agent") or "?",
                    "started_at_ts": row.get("started_at_ts"),
                    "ended_at_ts": row.get("ended_at_ts"),
                    "created_at_ts": row.get("created_at_ts"),
                    "result_preview": row.get("result_preview") or "",
                })
    except Exception as exc:
        _api_logger.warning("live trace events: %s", exc)

    # --- queue depth ---
    try:
        qq = get_q()
        queue_depth = len(qq)
    except Exception as exc:
        _api_logger.warning("live queue depth: %s", exc)

    neo.close()

    return {
        "ts": now,
        "fleet": {
            "nodes": fleet_nodes,
            "total": len(fleet_nodes),
            "online": sum(1 for n in fleet_nodes if n.get("status") == "online"),
        },
        "pipeline": {
            "task_counts": task_counts,
            "recent_runs_5m": recent_runs,
            "runs_last_hour": list(runs_last_hour.values()),
            "queue_depth": queue_depth,
        },
        "neo4j": {
            "label_counts": label_counts,
            "total_labels": len(label_counts),
        },
        "traces": {
            "events": recent_events,
        },
    }


@app.get("/live", response_class=HTMLResponse)
def live_dashboard(request: Request, user: str = Depends(auth)):
    return templates.TemplateResponse("live.html", {"request": request})


@app.get("/links.json")
def api_links(user: str = Depends(auth)):
    return {
        "assistx": {
            "name": "AssistX",
            "version": "2.0",
            "description": "Workflow control plane — intake, routing, review, memory, and live operator state",
        },
        "dashboard": {
            "live": {"href": "/live", "title": "Operational HQ — fleet, pipeline, traces"},
            "strategy": {"href": "/strategy", "title": "Strategic overseer — work buckets, objectives, steering"},
        },
        "pipeline": {
            "tasks": {"href": "/api/tasks", "title": "Task CRUD", "counts": "/api/live/dashboard#pipeline"},
            "runs": {"href": "/runs", "title": "Agent run history"},
            "queue": {"href": "/api/workflows/queue", "title": "Workflow queue by class"},
        },
        "fleet": {
            "dashboard": {"href": "/api/live/dashboard", "title": "Live aggregated dashboard JSON"},
            "nodes": {"href": "/api/fleet/nodes", "title": "Fleet node list"},
            "models": {"href": "/api/fleet/models", "title": "Model inventory across nodes"},
            "benchmarks": {"href": "/api/fleet/benchmarks", "title": "Benchmark data"},
            "loadouts": {"href": "/api/fleet/loadouts", "title": "Auto-router loadout report"},
        },
        "operations": {
            "health": {"href": "/health", "title": "Service health"},
            "status": {"href": "/api/ops/status", "title": "Operational status (queue, feeds, eval suites)"},
            "metrics": {"href": "/metrics", "title": "Prometheus metrics"},
            "dispatches": {"href": "/dispatches", "title": "Dispatch execution log"},
            "intents": {"href": "/intents", "title": "Raw incoming intents"},
            "review": {"href": "/review", "title": "Human review triage queue"},
            "tasks_ready": {"href": "/tasks/ready", "title": "READY tasks (approve/enqueue)"},
        },
        "knowledge": {
            "sessions": {"href": "/sessions", "title": "Agent sessions"},
            "memory": {"href": "/memory", "title": "Durable memory items"},
            "answers": {"href": "/answers", "title": "Q&A responses"},
            "qa_ask": {"href": "/api/ask", "title": "Ask a question (POST)", "methods": ["POST"]},
        },
        "data": {
            "ingest": {"href": "/ingest", "title": "Audio/video capture"},
            "devices": {"href": "/devices", "title": "Registered devices"},
        },
        "evaluation": {
            "suites": {"href": "/api/evaluations/suites", "title": "Evaluation suite definitions"},
            "runs": {"href": "/api/evaluations", "title": "Evaluation run records"},
        },
        "swarm": {
            "nodes": {"href": "/api/swarm/nodes", "title": "Swarm node registrations"},
            "endpoints": {"href": "/api/swarm/model-endpoints", "title": "Model endpoint registry"},
        },
    }


# ---------------------------------------------------------------------------
# Strategy — work categorization and steering
# ---------------------------------------------------------------------------

@app.get("/api/live/strategy")
def api_live_strategy(user: str = Depends(auth)):
    """Strategic overview: work buckets, priorities, deliverable trees, agent utilization."""
    neo = _neo()
    now = _now_ts()
    one_hour_ago = now - 3_600_000
    twenty_four_hours = now - 86_400_000

    work_by_kind = {}
    work_by_priority = {}
    work_by_queue_class = {}
    agent_utilization = {}
    intent_inflow = {}
    deliverable_trees = []
    guidance_list = []

    try:
        with neo._session() as s:
            # Work by kind x status
            res = s.run("""
                MATCH (t:Task)
                RETURN coalesce(t.kind, 'uncategorized') AS kind,
                       coalesce(t.status, 'UNKNOWN') AS status,
                       count(t) AS cnt
                ORDER BY kind, status
            """)
            for row in res:
                k = row["kind"]
                st = row["status"]
                cnt = row["cnt"]
                if k not in work_by_kind:
                    work_by_kind[k] = {"kind": k, "total": 0, "by_status": {}}
                work_by_kind[k]["by_status"][st] = cnt
                work_by_kind[k]["total"] += cnt

            # Work by priority x status
            res = s.run("""
                MATCH (t:Task)
                RETURN coalesce(t.priority, 'UNSET') AS priority,
                       coalesce(t.status, 'UNKNOWN') AS status,
                       count(t) AS cnt
                ORDER BY priority, status
            """)
            for row in res:
                p = row["priority"]
                st = row["status"]
                cnt = row["cnt"]
                if p not in work_by_priority:
                    work_by_priority[p] = {"priority": p, "total": 0, "by_status": {}}
                work_by_priority[p]["by_status"][st] = cnt
                work_by_priority[p]["total"] += cnt

            # Agent runs (last 24h) by agent x status
            res = s.run("""
                MATCH (r:AgentRun)
                WHERE r.created_at_ts > $since
                RETURN coalesce(r.agent, 'unknown') AS agent,
                       coalesce(r.status, 'UNKNOWN') AS status,
                       count(r) AS cnt
                ORDER BY agent, status
            """, since=twenty_four_hours)
            for row in res:
                a = row["agent"]
                st = row["status"]
                cnt = row["cnt"]
                if a not in agent_utilization:
                    agent_utilization[a] = {"agent": a, "total": 0, "by_status": {}}
                agent_utilization[a]["by_status"][st] = cnt
                agent_utilization[a]["total"] += cnt

            # Intent inflow (last 1h)
            res = s.run("""
                MATCH (i:Intent)
                WHERE i.created_at_ts > $since
                RETURN coalesce(i.classification, 'unknown') AS classification,
                       count(i) AS cnt
                ORDER BY cnt DESC
            """, since=one_hour_ago)
            for row in res:
                intent_inflow[row["classification"]] = row["cnt"]

            # Active deliverable trees
            res = s.run("""
                MATCH (d:Task {ticket_type: 'deliverable'})
                WHERE d.status IN ['READY', 'RUNNING', 'REVIEW']
                OPTIONAL MATCH path = (d)-[:HAS_CHILD*1..3]->(child:Task)
                WITH d, collect(DISTINCT child) AS children
                RETURN d.id AS id, d.title AS title, d.status AS status,
                       d.priority AS priority, d.kind AS kind,
                       [c IN children WHERE c.ticket_type = 'epic' | {
                           id: c.id, title: c.title, status: c.status,
                           priority: c.priority
                       }] AS epics,
                       size([c IN children WHERE c.ticket_type = 'story']) AS story_count,
                       size([c IN children WHERE c.ticket_type = 'task']) AS task_count
                ORDER BY d.created_at_ts DESC
                LIMIT 20
            """)
            for row in res:
                deliverable_trees.append({
                    "id": row.get("id"),
                    "title": row.get("title"),
                    "status": row.get("status"),
                    "priority": row.get("priority"),
                    "kind": row.get("kind"),
                    "epics": row.get("epics") or [],
                    "story_count": row.get("story_count") or 0,
                    "task_count": row.get("task_count") or 0,
                })

            # Guidance / objectives
            res = s.run("""
                MATCH (g:Guidance)
                WHERE g.active = true
                RETURN g.id AS id, g.type AS type,
                       g.target AS target, g.message AS message,
                       g.priority AS priority,
                       g.created_at_ts AS created_at_ts,
                       g.created_by AS created_by
                ORDER BY g.created_at_ts DESC
            """)
            for row in res:
                guidance_list.append({
                    "id": row.get("id"),
                    "type": row.get("type"),
                    "target": row.get("target"),
                    "message": row.get("message"),
                    "priority": row.get("priority"),
                    "created_at_ts": row.get("created_at_ts"),
                    "created_by": row.get("created_by"),
                })
    except Exception as exc:
        _api_logger.warning("strategy query: %s", exc)
    finally:
        neo.close()

    # --- Model routing health ---
    # Probe fleet nodes from Neo4j (not LLM client's fleet_state.json which
    # may not exist inside the container) to determine which models are hot.
    model_routing = {"hot_models": {}, "blacklisted_pairs": [], "refresh_available": True}
    try:
        fleet_nodes_for_probe: List[dict] = []
        neo2 = _neo()
        try:
            with neo2._session() as s:
                res = s.run("""
                    MATCH (m:ModelEndpoint)
                    RETURN m.base_url AS base_url
                """)
                for row in res:
                    url = row.get("base_url") or ""
                    if url:
                        fleet_nodes_for_probe.append({"base_url": url})
        finally:
            neo2.close()

        if fleet_nodes_for_probe:
            live = _probe_nodes_parallel(fleet_nodes_for_probe)
            # Aggregate hot models across all probed nodes
            hot_by_model: dict = {}
            for url, result in live.items():
                if result and result.get("online") and result.get("loaded_models"):
                    for mid in result["loaded_models"]:
                        url_short = url.rsplit("/v1", 1)[0] if "/v1" in url else url
                        hot_by_model.setdefault(mid, []).append(url_short)
            for mid, urls in sorted(hot_by_model.items(), key=lambda x: -len(x[1])):
                model_routing["hot_models"][mid] = {"nodes": len(urls), "urls": urls}

            # Soft-suppressed (model, node) pairs from the LLM client.
            from assistx.llm import client as llm_client
            suppressed = llm_client._pair_failures if hasattr(llm_client, "_pair_failures") else {}
            for pair_key, fails in sorted(suppressed.items()):
                if fails >= llm_client._PAIR_HARD_FAIL_LIMIT:
                    model_routing["blacklisted_pairs"].append({
                        "pair": pair_key, "failures": fails
                    })
            model_routing["loader"] = llm_client.get_loader_state()
            # Learned quality×speed from the KG.
            try:
                with neo2._session() as s2:
                    res = s2.run("""
                        MATCH (p:ModelPerf)
                        RETURN p.model AS model, avg(p.quality_score) AS q,
                               avg(p.latency_ms) AS lat, count(p) AS runs
                        GROUP BY p.model
                    """)
                    perf = {}
                    for row in res:
                        perf[row["model"]] = {
                            "quality": round(row.get("q") or 0.0, 3),
                            "latency_ms": round(row.get("lat") or 0.0, 1),
                            "runs": row.get("runs") or 0,
                        }
                    model_routing["learned_perf"] = perf
            except Exception:
                model_routing["learned_perf"] = {}
    except Exception as exc:
        model_routing["error"] = str(exc)[:120]

    return {
        "ts": now,
        "work_by_kind": sorted(work_by_kind.values(), key=lambda x: -x["total"]),
        "work_by_priority": sorted(work_by_priority.values(), key=_priority_sort_key),
        "agent_utilization": sorted(agent_utilization.values(), key=lambda x: -x["total"]),
        "intent_inflow": intent_inflow,
        "deliverable_trees": deliverable_trees,
        "guidance": guidance_list,
        "model_routing": model_routing,
    }


def _priority_sort_key(item):
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "BACKGROUND": 4, "BATCH": 5, "UNSET": 9}
    return order.get(item["priority"].upper(), 9)


# ---------------------------------------------------------------------------
# Steering — guidance and objectives
# ---------------------------------------------------------------------------

class GuidanceIn(BaseModel):
    type: str = "objective"
    target: str = "all"
    message: str
    priority: str = "MEDIUM"


@app.get("/api/steering/guidance")
def api_get_guidance(user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo._session() as s:
            res = s.run("""
                MATCH (g:Guidance)
                WHERE g.active = true
                RETURN g.id AS id, g.type AS type,
                       g.target AS target, g.message AS message,
                       g.priority AS priority,
                       g.created_at_ts AS created_at_ts,
                       g.created_by AS created_by
                ORDER BY g.created_at_ts DESC
            """)
            items = []
            for row in res:
                items.append({
                    "id": row.get("id"),
                    "type": row.get("type"),
                    "target": row.get("target"),
                    "message": row.get("message"),
                    "priority": row.get("priority"),
                    "created_at_ts": row.get("created_at_ts"),
                    "created_by": row.get("created_by"),
                })
            return {"guidance": items}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        neo.close()


@app.post("/api/steering/guidance")
def api_set_guidance(body: GuidanceIn, user: str = Depends(auth)):
    neo = _neo()
    gid = uuid.uuid4().hex
    now = _now_ts()
    try:
        with neo._session() as s:
            s.run("""
                CREATE (g:Guidance {
                    id: $id, type: $type, target: $target,
                    message: $message, priority: $priority,
                    active: true,
                    created_at_ts: $now, created_by: $user
                })
            """, id=gid, type=body.type, target=body.target,
                 message=body.message, priority=body.priority.upper(),
                 now=now, user=user)
        return {"id": gid, "created_at_ts": now}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        neo.close()


@app.delete("/api/steering/guidance/{gid}")
def api_delete_guidance(gid: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo._session() as s:
            result = s.run("""
                MATCH (g:Guidance {id: $id})
                SET g.active = false, g.deactivated_at_ts = $now,
                    g.deactivated_by = $user
                RETURN g.id AS id
            """, id=gid, now=_now_ts(), user=user)
            if not result.single():
                raise HTTPException(status_code=404, detail="Guidance not found")
            return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        neo.close()


@app.get("/strategy", response_class=HTMLResponse)
def strategy_page(request: Request, user: str = Depends(auth)):
    return templates.TemplateResponse("strategy.html", {"request": request})


@app.get("/api/ops/status")
def api_ops_status(
    stale_minutes: int = Query(30, ge=1, le=24 * 60),
    review_sla_minutes: int = Query(60, ge=1, le=24 * 60),
    review_backlog_threshold: int = Query(25, ge=1, le=2000),
    user: str = Depends(auth),
):
    runtime = build_runtime_health()
    queue_depth = 0
    running_count = 0
    failed_count = 0
    try:
        q = get_q()
        queue_depth = len(q)
        running_count = q.started_job_registry.count
        failed_count = q.failed_job_registry.count
        if callable(running_count):
            running_count = running_count()
        if callable(failed_count):
            failed_count = failed_count()
    except Exception:
        pass

    neo_health = "ok"
    stale_sessions = 0
    failed_dispatches = 0
    review_backlog = 0
    oldest_review_age_minutes = 0.0
    review_sla_breached = False
    workflow_backlog = 0
    workflow_running = 0
    escalation_backlog = 0
    feeds = {"total": 0, "enabled": 0, "by_status": {"healthy": 0, "degraded": 0, "down": 0}, "connectors": []}
    evaluation_suites = {"total": 0, "enabled": 0, "by_agent_class": {}, "suites": []}
    try:
        feeds = feed_health_summary()
    except Exception:
        pass
    try:
        evaluation_suites = suites_summary()
    except Exception:
        pass
    try:
        neo = _neo()
        with neo._session() as s:
            stale_rec = s.run(
                """
                MATCH (sess:AgentSession)
                WHERE coalesce(sess.last_seen_at_ts, sess.updated_at_ts, sess.created_at_ts, 0)
                      < (timestamp() - ($mins * 60 * 1000))
                RETURN count(sess) AS cnt
                """,
                {"mins": stale_minutes},
            ).single()
            stale_sessions = int(stale_rec["cnt"] if stale_rec else 0)
            fail_rec = s.run(
                """
                MATCH (d:Dispatch)
                WHERE d.status IN ['FAILED','CANCELLED']
                RETURN count(d) AS cnt
                """
            ).single()
            failed_dispatches = int(fail_rec["cnt"] if fail_rec else 0)
            review_rec = s.run(
                """
                MATCH (t:Task)
                WHERE t.status='REVIEW'
                RETURN
                    count(t) AS cnt,
                    min(coalesce(t.created_at_ts, t.updated_at_ts, t.reviewed_at_ts, timestamp())) AS oldest_created_ts
                """
            ).single()
            review_backlog = int(review_rec["cnt"] if review_rec else 0)
            oldest_ts = int(review_rec["oldest_created_ts"] if review_rec and review_rec["oldest_created_ts"] is not None else 0)
            if review_backlog > 0 and oldest_ts > 0:
                oldest_review_age_minutes = max(0.0, (int(_time.time() * 1000) - oldest_ts) / 60000.0)
                review_sla_breached = oldest_review_age_minutes > float(review_sla_minutes)
            if review_backlog > int(review_backlog_threshold):
                review_sla_breached = True
            wf_rec = s.run(
                """
                MATCH (t:Task)
                WHERE t.status IN ['READY','CLAIMED','RUNNING']
                RETURN
                    count(t) AS backlog,
                    sum(CASE WHEN t.status='RUNNING' THEN 1 ELSE 0 END) AS running
                """
            ).single()
            workflow_backlog = int(wf_rec["backlog"] if wf_rec and wf_rec["backlog"] is not None else 0)
            workflow_running = int(wf_rec["running"] if wf_rec and wf_rec["running"] is not None else 0)
            esc_rec = s.run(
                """
                MATCH (t:Task)
                WHERE t.kind='intent_review'
                  AND t.status='REVIEW'
                  AND coalesce(t.policy_action, '') IN ['review_dispatch','review_cancel','needs_clarification']
                RETURN count(t) AS cnt
                """
            ).single()
            escalation_backlog = int(esc_rec["cnt"] if esc_rec else 0)
    except Exception:
        neo_health = "degraded"
    finally:
        try:
            neo.close()
        except Exception:
            pass

    return {
        "neo4j": {"status": neo_health},
        "queue": {
            "depth": int(queue_depth),
            "running": int(running_count),
            "failed": int(failed_count),
        },
        "dispatches": {"failed_or_cancelled": failed_dispatches},
        "sessions": {"stale": stale_sessions, "stale_threshold_minutes": stale_minutes},
        "review": {
            "backlog": review_backlog,
            "backlog_threshold": int(review_backlog_threshold),
            "oldest_age_minutes": round(oldest_review_age_minutes, 2),
            "sla_minutes": int(review_sla_minutes),
            "sla_breached": bool(review_sla_breached),
        },
        "workflow": {
            "backlog": workflow_backlog,
            "running": workflow_running,
            "escalation_backlog": escalation_backlog,
            "control": _get_workflow_control(),
        },
        "feeds": feeds,
        "evaluation_suites": evaluation_suites,
        "runtime": runtime,
    }

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

if MULTIPART_AVAILABLE:
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

        whisper_model_used: Optional[str] = None
        if not transcript.strip() and kind in ("audio", "video") and media_path:
            try:
                model_name = os.getenv("WHISPER_FALLBACK_MODEL", "tiny")
                wm = get_whisper_model(model_name)
                segments, info = wm.transcribe(media_path, beam_size=1)
                seg_texts = [(seg.text or "").strip() for seg in segments]
                transcript = "\n".join(t for t in seg_texts if t)
                whisper_model_used = model_name
            except Exception as e:
                _api_logger.warning("Whisper fallback transcription failed for %s: %s", capture_id, e)

        classification = classify_text(transcript) if transcript.strip() else None
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
                metadata={
                    "authenticated_user": user,
                    "whisper_fallback": whisper_model_used is not None,
                },
                intent_classification=classification,
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
            "whisper_fallback_model": whisper_model_used,
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
else:
    @app.post("/api/captures")
    async def api_create_capture(user: str = Depends(auth)):
        raise HTTPException(status_code=503, detail="python-multipart is required for /api/captures")

    @app.post("/upload-audio")
    async def upload_audio(user: str = Depends(auth)):
        raise HTTPException(status_code=503, detail="python-multipart is required for /upload-audio")

@app.post("/api/tasks")
def api_create_task(body: TaskCreateIn, user: str = Depends(auth)):
    """Create a READY swarm task for fleet nodes to execute.

    Returns the created task_id. If ``task_id`` is supplied and already exists
    (idempotent), the existing task is returned without duplication.
    """
    neo = _neo()
    try:
        payload = dict(body.payload or {})
        if body.correlation_id:
            payload.setdefault("correlation_id", body.correlation_id)
        result = neo.create_task_with_context(
            title=body.title,
            task_type=body.task_type,
            status=body.status,
            kind=body.kind,
            required_capabilities=body.required_capabilities,
            target_agent_id=body.target_agent_id,
            priority=body.priority,
            payload=payload,
            idempotency_key=body.idempotency_key,
        )
        return {"task_id": result.get("task_id"), "dispatch_id": result.get("dispatch_id")}
    finally:
        neo.close()


@app.get("/api/tasks")
def api_list_tasks(
    status: Optional[str] = Query(None, description="filter by status"),
    limit: int = Query(50, ge=1, le=500),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        with neo._session() as s:
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
        with neo._session() as s:
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


@app.post("/api/tasks/{task_id}/enqueue")
def api_enqueue_task(task_id: str, dry_run: bool = False, user: str = Depends(auth)):
    """JSON enqueue endpoint used by auto-assign / fleet drain.

    Mirrors the HTML ``/tasks/{task_id}/enqueue`` route but returns a
    machine-readable payload and 404s when the task does not exist. Returns
    409 when the task already has a queued/started job so callers can treat
    it as idempotent."""
    neo = _neo()
    try:
        with neo._session() as s:
            rec = s.run(
                "MATCH (t:Task {id:$id}) RETURN t.status AS status",
                {"id": task_id},
            ).single()
            if not rec:
                raise HTTPException(status_code=404, detail="Task not found")
            status = rec["status"]
        if status in ("RUNNING", "SUCCESS", "FAILURE", "COMPLETED"):
            return {"enqueued": False, "already": True, "status_code": 409, "task_id": task_id}
        q = get_q()
        job = q.enqueue(execute_task_job, task_id, dry_run)
        EXECUTIONS.labels(status="ENQUEUED").inc()
        return {"enqueued": True, "job_id": job.get_id(), "task_id": task_id}
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
    neo = _neo_fleet()
    items = neo.list_agent_tasks(
        status=status,
        capabilities=capabilities,
        agent_id=agent_id,
        limit=limit,
    )
    runtime = _workflow_runtime_snapshot(neo)
    admitted = _apply_workflow_admission(items, runtime)
    return {
        "items": admitted,
        "count": len(admitted),
        "admission": {
            **_get_workflow_control(),
            "running": runtime.get("running"),
            "batch_ready_direct": runtime.get("batch_ready_direct"),
        },
    }

@app.post("/api/tasks/{task_id}/claim")
def api_claim_task(task_id: str, body: TaskClaimIn, user: str = Depends(auth)):
    neo = _neo_fleet()
    task_obj = neo.get_task(task_id)
    if not task_obj:
        raise HTTPException(status_code=404, detail="Task not found")
    allowed, reason = _is_claim_allowed_for_workflow_control(task_obj)
    if not allowed:
        TASK_CLAIMS.labels(result="drain_blocked").inc()
        raise HTTPException(status_code=409, detail={"claimed": False, "reason": "drain_mode_block", "message": reason})
    result = neo._with_retry(
        lambda: neo.claim_task(
            task_id=task_id,
            agent_id=body.agent_id,
            capabilities=body.capabilities,
            session_id=body.session_id,
            idempotency_key=body.idempotency_key,
            lease_seconds=body.lease_seconds,
        )
    )
    if result.get("claimed"):
        TASK_CLAIMS.labels(result="claimed").inc()
        return result
    TASK_CLAIMS.labels(result=result.get("reason", "conflict")).inc()
    if result.get("reason") == "not_found":
        raise HTTPException(status_code=404, detail="Task not found")
    raise HTTPException(status_code=409, detail=result)

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
            lease_seconds=body.lease_seconds,
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
    neo = _neo_fleet()
    task = neo._with_retry(
        lambda: neo.complete_task(
            task_id=task_id,
            agent_id=body.agent_id,
            status=body.status,
            summary=body.summary,
            result=body.result,
            session_id=body.session_id,
            idempotency_key=body.idempotency_key,
        )
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    dead_letter_incident_id = _maybe_dead_letter_exhausted_task(
        neo=neo,
        task_id=task_id,
        completed_status=body.status,
        actor=user,
    )
    if dead_letter_incident_id:
        refreshed = neo.get_task(task_id)
        if refreshed:
            task = refreshed
    TASK_COMPLETIONS.labels(status=body.status).inc()
    return {"task": task, "dead_letter_incident_id": dead_letter_incident_id}


@app.post("/api/paperclip/events")
def api_paperclip_event(
    body: PaperclipEventIn,
    x_paperclip_signature: Optional[str] = Header(None),
):
    _verify_paperclip_signature(body, x_paperclip_signature)
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

@app.post("/api/voice/events")
def api_voice_event(
    request: Request,
    body: VoiceEventIn,
    credentials: HTTPBasicCredentials | None = Depends(security),
    x_voice_signature: Optional[str] = Header(None),
):
    # Allow either operator auth (Basic/trusted header) or signed callback auth.
    user = _auth_user_from_credentials(request, credentials)
    if user is None:
        _verify_voice_signature(body, x_voice_signature)
        user = "voice_webhook"
    event_payload = {
        "event_id": body.event_id,
        "event_type": body.event_type,
        "text": body.text or "",
        "source": body.source,
        "client_ts": body.client_ts,
        "metadata": body.metadata or {},
    }
    neo = _neo()
    try:
        signal_id = neo.create_signal_event(
            event_id=body.event_id,
            event_type=body.event_type,
            payload=event_payload,
            session_id=body.session_id,
        )

        # Generate or extract correlation_id for trace linkage
        meta = body.metadata or {}
        correlation_id = meta.get("correlation_id") or f"corr:{body.event_id}"
        canonical_trace_type = _canonical_voice_trace_type(body.event_type, body.text)

        # Record canonical trace event
        record_trace_event(
            neo,
            correlation_id=correlation_id,
            event_type=canonical_trace_type,
            source="sophia_voice",
            task_id=None,
            dispatch_id=None,
            payload={
                "signal_event_id": signal_id,
                "event_type": body.event_type,
                "text": body.text or "",
                "session_id": body.session_id,
                "user_id": meta.get("user_id", "scott"),
                "device_id": meta.get("device_id"),
                "score": meta.get("score"),
                "accepted": meta.get("accepted"),
                "match_source": meta.get("match_source"),
            },
        )

        created_intent_id: Optional[str] = None
        created_memory_id: Optional[str] = None
        created_task_id: Optional[str] = None
        cancelled_tasks = 0
        text = (body.text or "").strip()
        if text:
            classification = classify_text(text)
            intent_outcome, intent_confidence = _intent_outcome_and_confidence(text, classification)
            policy_action = _intent_policy_action(intent_outcome, intent_confidence)
            handles_inline_task = (
                classification == CLASSIFICATION_TASK
                and body.event_type in {"task_created", "meeting_transcript"}
            )
            intent_key = f"voice:{body.event_id}"
            created_intent_id = neo.upsert_intent(
                source=body.source,
                text=text,
                idempotency_key=intent_key,
                client_ts=body.client_ts,
                metadata={
                    "voice_event_type": body.event_type,
                    "session_id": body.session_id,
                    "policy_action": policy_action,
                    "correlation_id": correlation_id,
                    **(body.metadata or {}),
                },
                classification=classification,
                intent_outcome=intent_outcome,
                intent_confidence=intent_confidence,
                mark_orchestrated=handles_inline_task,
            )

            if classification in (CLASSIFICATION_MEMORY, CLASSIFICATION_QUERY):
                created_memory_id = neo.upsert_memory_item(
                    kind="voice_note" if classification == CLASSIFICATION_MEMORY else "voice_query",
                    text=text,
                    source=body.source,
                    session_id=body.session_id,
                    metadata={
                        "voice_event_id": body.event_id,
                        "voice_event_type": body.event_type,
                        "classification": classification,
                        "correlation_id": correlation_id,
                    },
                )

            if classification == CLASSIFICATION_CANCEL:
                cancelled_tasks = _cancel_tasks_for_intent(
                    neo,
                    created_intent_id,
                    f"Cancelled by voice event {body.event_type}",
                )
            if handles_inline_task:
                task_res = neo.create_task_with_context(
                    title=(text[:120] + "...") if len(text) > 120 else text,
                    task_type="task",
                    kind="sophia_voice",
                    required_capabilities=["terminal"],
                    payload={
                        "source_event_id": body.event_id,
                        "source_intent": created_intent_id,
                        "voice_event_type": body.event_type,
                        "correlation_id": correlation_id,
                    },
                    context_query=text,
                    context_sources=["memory", "knowledge", "orchestration"],
                    idempotency_key=f"voice-task:{body.event_id}",
                    auto_dispatch=False,
                )
                created_task_id = task_res["task_id"]
                with neo._session() as s:
                    s.run(
                        "MATCH (i:Intent {id:$iid}), (t:Task {id:$tid}) "
                        "MERGE (i)-[:CREATED_TASK]->(t)",
                        {"iid": created_intent_id, "tid": created_task_id},
                    ).consume()

                # Record dispatch.requested trace event if auto-dispatch
                if body.auto_dispatch:
                    record_trace_event(
                        neo,
                        correlation_id=correlation_id,
                        event_type="dispatch.requested",
                        source="sophia_voice",
                        task_id=created_task_id,
                        payload={"source_event_id": body.event_id},
                    )
                    neo.create_dispatch_with_paperclip(
                        task_id=created_task_id,
                        target={
                            "capabilities": ["terminal"],
                            "paperclip_agent_id": PAPERCLIP_AGENT_ID,
                        },
                        idempotency_key=f"voice-dispatch:{body.event_id}",
                        paperclip_client=get_paperclip_client(),
                    )
                    record_trace_event(
                        neo,
                        correlation_id=correlation_id,
                        event_type="dispatch.accepted",
                        source="assistx",
                        task_id=created_task_id,
                        payload={"dispatch_method": "paperclip"},
                    )

        if body.event_type in {"cancel_active", "task_cancelled", "barge_in"} and not created_intent_id:
            intent_outcome, intent_confidence = _intent_outcome_and_confidence(
                text or f"{body.event_type} requested",
                CLASSIFICATION_CANCEL,
            )
            policy_action = _intent_policy_action(intent_outcome, intent_confidence)
            cancel_intent_id = neo.upsert_intent(
                source=body.source,
                text=text or f"{body.event_type} requested",
                idempotency_key=f"voice-cancel:{body.event_id}",
                client_ts=body.client_ts,
                metadata={
                    "voice_event_type": body.event_type,
                    "policy_action": policy_action,
                    "correlation_id": correlation_id,
                    **(body.metadata or {}),
                },
                classification=CLASSIFICATION_CANCEL,
                intent_outcome=intent_outcome,
                intent_confidence=intent_confidence,
            )
            cancelled_tasks = _cancel_tasks_for_intent(
                neo,
                cancel_intent_id,
                f"Cancelled by voice event {body.event_type}",
            )
            created_intent_id = cancel_intent_id

        metadata = body.metadata or {}
        neo.link_sophia_voice_records(
            capture_id=str(metadata.get("capture_id") or "").strip() or None,
            intent_id=created_intent_id,
            memory_id=created_memory_id,
            task_id=created_task_id,
            meeting_id=str(metadata.get("meeting_id") or "").strip() or None,
        )

        return {
            "signal_event_id": signal_id,
            "intent_id": created_intent_id,
            "memory_item_id": created_memory_id,
            "task_id": created_task_id,
            "cancelled_tasks": cancelled_tasks,
            "correlation_id": correlation_id,
            "trace_url": f"/api/traces/{correlation_id}",
        }
    finally:
        neo.close()


def _canonical_voice_trace_type(event_type: str, text: Optional[str] = None) -> str:
    """Map legacy voice event types to canonical trace event types."""
    mapping = {
        "voice_auth": "voice.auth.accepted",
        "task_created": "dispatch.requested",
        "meeting_transcript": "dispatch.requested",
        "cancel_active": "dispatch.cancelled",
        "task_cancelled": "dispatch.cancelled",
        "barge_in": "dispatch.cancelled",
        "ralph_iteration": "dispatch.requested",
        "tts_chunk": "voice.auth.requested",
        "voice_enrolled": "voice.auth.accepted",
        "speaker_identified": "voice.auth.accepted",
    }
    return mapping.get(event_type, "voice.auth.requested")


@app.post("/api/sophia/events")
def api_sophia_event(body: SophiaVoiceEventIn, user: str = Depends(auth)):
    allowed_auth = {None, "authenticated_scott", "not_scott_known", "unknown_unverified"}
    if body.auth_state not in allowed_auth:
        raise HTTPException(status_code=400, detail="Unsupported auth_state")

    routing_policy = _sophia_routing_policy()
    routing_policy_fingerprint = _sophia_routing_policy_fingerprint(routing_policy)
    qclass = _sophia_queue_class(body.event_type, body.auth_state)
    payload = {
        "source": "sophia_voice",
        "event_type": body.event_type,
        "session_id": body.session_id,
        "auth_state": body.auth_state,
        "speaker_identity": body.speaker_identity,
        "speaker_confidence": body.speaker_confidence,
        "policy_version": body.policy_version,
        "transcript_text": body.transcript_text,
        "queue_class": qclass,
        "routing_policy": routing_policy,
        "routing_policy_fingerprint": routing_policy_fingerprint,
        "payload": body.payload,
        "metadata": body.metadata or {},
    }
    neo = _neo()
    try:
        policy_change_incident_id = _record_sophia_policy_change_if_needed(
            neo=neo,
            user=user,
            policy=routing_policy,
            source="api_sophia_event",
        )
        signal_id = neo.create_signal_event(
            event_id=body.event_id,
            event_type=f"sophia_{body.event_type}",
            payload=payload,
            session_id=body.session_id,
        )

        created_task_id: Optional[str] = None
        created_intent_id: Optional[str] = None
        incident_id: Optional[str] = None

        text = (body.transcript_text or "").strip()
        if text:
            classification = classify_text(text)
            intent_outcome, intent_confidence = _intent_outcome_and_confidence(text, classification)
            policy_action = _intent_policy_action(intent_outcome, intent_confidence)
            handles_inline_task = (
                classification == CLASSIFICATION_TASK
                and body.event_type in {"intent", "voice_chat", "meeting_action_items"}
            )
            created_intent_id = neo.upsert_intent(
                source="sophia_voice",
                text=text,
                idempotency_key=f"sophia:{body.event_id}",
                metadata={
                    "session_id": body.session_id,
                    "auth_state": body.auth_state,
                    "speaker_identity": body.speaker_identity,
                    "policy_version": body.policy_version,
                    "policy_action": policy_action,
                    "queue_class": qclass,
                    "routing_policy_fingerprint": routing_policy_fingerprint,
                    **(body.metadata or {}),
                },
                classification=classification,
                intent_outcome=intent_outcome,
                intent_confidence=intent_confidence,
                mark_orchestrated=handles_inline_task,
            )

            if handles_inline_task:
                task_res = neo.create_task_with_context(
                    title=(text[:120] + "...") if len(text) > 120 else text,
                    task_type="task",
                    kind="sophia_voice",
                    required_capabilities=["terminal"],
                    payload={
                        "source_event_id": body.event_id,
                        "source_intent": created_intent_id,
                        "queue_class": qclass,
                        "auth_state": body.auth_state,
                        "speaker_identity": body.speaker_identity,
                    },
                    context_query=text,
                    context_sources=["memory", "knowledge", "orchestration"],
                    idempotency_key=f"sophia-task:{body.event_id}",
                    auto_dispatch=False,
                )
                created_task_id = task_res["task_id"]
                with neo._session() as s:
                    s.run(
                        "MATCH (i:Intent {id:$iid}), (t:Task {id:$tid}) "
                        "MERGE (i)-[:CREATED_TASK]->(t)",
                        {"iid": created_intent_id, "tid": created_task_id},
                    ).consume()
                if qclass != "critical":
                    neo.create_dispatch_with_paperclip(
                        task_id=created_task_id,
                        target={
                            "capabilities": ["terminal"],
                            "paperclip_agent_id": PAPERCLIP_AGENT_ID,
                        },
                        idempotency_key=f"sophia-dispatch:{body.event_id}",
                        paperclip_client=get_paperclip_client(),
                    )

        if body.auth_state in {"not_scott_known", "unknown_unverified"}:
            incident_id = neo.create_workflow_incident(
                workflow_id=created_task_id or (created_intent_id or body.event_id),
                incident_type="auth_state_anomaly",
                severity="warning",
                detail=f"Sophia auth_state={body.auth_state}",
                metadata={
                    "event_id": body.event_id,
                    "speaker_identity": body.speaker_identity,
                    "speaker_confidence": body.speaker_confidence,
                    "queue_class": qclass,
                    "routing_policy_fingerprint": routing_policy_fingerprint,
                },
            )

        return {
            "signal_event_id": signal_id,
            "intent_id": created_intent_id,
            "task_id": created_task_id,
            "queue_class": qclass,
            "routing_policy_fingerprint": routing_policy_fingerprint,
            "incident_id": incident_id,
            "policy_change_incident_id": policy_change_incident_id,
        }
    finally:
        neo.close()


@app.get("/api/sophia/summary")
def api_sophia_summary(
    limit: int = Query(300, ge=10, le=5000),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        _record_sophia_policy_change_if_needed(
            neo=neo,
            user=user,
            policy=_sophia_routing_policy(),
            source="api_sophia_summary",
        )
        auth_states: Dict[str, int] = {
            "authenticated_scott": 0,
            "not_scott_known": 0,
            "unknown_unverified": 0,
            "unknown": 0,
        }
        by_event_type: Dict[str, int] = {}
        by_queue_class: Dict[str, int] = {"interactive": 0, "batch": 0, "critical": 0, "unknown": 0}

        with neo._session() as s:
            rows = s.run(
                """
                MATCH (e:SignalEvent)
                WHERE coalesce(e.event_type, '') STARTS WITH 'sophia_'
                RETURN e
                ORDER BY coalesce(e.created_at_ts, e.updated_at_ts, 0) DESC
                LIMIT $limit
                """,
                {"limit": limit},
            )
            items = [dict(r["e"]) for r in rows]

            for ev in items:
                payload = _json_dict(ev.get("payload_json"))
                st = str(payload.get("auth_state") or "unknown")
                if st not in auth_states:
                    st = "unknown"
                auth_states[st] += 1

                et = str(payload.get("event_type") or ev.get("event_type") or "unknown")
                by_event_type[et] = by_event_type.get(et, 0) + 1

                qclass = str(payload.get("queue_class") or "unknown")
                if qclass not in by_queue_class:
                    qclass = "unknown"
                by_queue_class[qclass] += 1

            incident_rec = s.run(
                """
                MATCH (w:WorkflowIncident)
                WHERE w.incident_type='auth_state_anomaly'
                RETURN count(w) AS cnt
                """
            ).single()
            auth_anomaly_incidents = int(incident_rec["cnt"] if incident_rec else 0)

        return {
            "sample_size": len(items),
            "routing_policy": _sophia_routing_policy(),
            "routing_policy_fingerprint": _sophia_routing_policy_fingerprint(),
            "auth_states": auth_states,
            "by_event_type": by_event_type,
            "by_queue_class": by_queue_class,
            "auth_anomaly_incidents": auth_anomaly_incidents,
        }
    finally:
        neo.close()


@app.get("/api/sophia/policy")
def api_sophia_policy(user: str = Depends(auth)):
    policy = _sophia_routing_policy()
    neo = _neo()
    try:
        policy_change_incident_id = _record_sophia_policy_change_if_needed(
            neo=neo,
            user=user,
            policy=policy,
            source="api_sophia_policy",
        )
        return {
            "routing_policy": policy,
            "routing_policy_fingerprint": _sophia_routing_policy_fingerprint(policy),
            "policy_change_incident_id": policy_change_incident_id,
        }
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
        with neo._session() as s:
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
        with neo._session() as s:
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


@app.get("/api/workflows/queue")
def api_workflows_queue(
    limit: int = Query(500, ge=1, le=2000),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        with neo._session() as s:
            rows = s.run(
                """
                MATCH (t:Task)
                WHERE t.status IN ['READY','CLAIMED','RUNNING','REVIEW']
                RETURN t
                ORDER BY coalesce(t.created_at_ts, t.updated_at_ts, 0) DESC
                LIMIT $limit
                """,
                {"limit": limit},
            )
            by_queue = {"interactive": 0, "batch": 0, "critical": 0, "unknown": 0}
            by_status: Dict[str, int] = {}
            items: List[Dict[str, Any]] = []
            for r in rows:
                task = dict(r["t"])
                payload = _json_dict(task.get("payload_json"))
                qclass = str(payload.get("queue_class") or task.get("queue_class") or "unknown")
                if qclass not in by_queue:
                    qclass = "unknown"
                by_queue[qclass] += 1
                st = str(task.get("status") or "UNKNOWN")
                by_status[st] = by_status.get(st, 0) + 1
                task["queue_class"] = qclass
                items.append(task)
        return {
            "control": _get_workflow_control(),
            "by_queue_class": by_queue,
            "by_status": by_status,
            "items": items[:100],
            "count": len(items),
        }
    finally:
        neo.close()


@app.get("/api/workflows/slo")
def api_workflows_slo(
    window_hours: int = Query(24, ge=1, le=168),
    user: str = Depends(auth),
):
    cutoff = int(_time.time() * 1000) - (window_hours * 60 * 60 * 1000)
    neo = _neo()
    try:
        with neo._session() as s:
            rows = s.run(
                """
                MATCH (t:Task)
                WHERE coalesce(t.created_at_ts, t.updated_at_ts, 0) >= $cutoff
                RETURN t
                """,
                {"cutoff": cutoff},
            )
            start_latencies: List[float] = []
            completion_latencies: List[float] = []
            completed = 0
            failed = 0
            total = 0
            for r in rows:
                t = dict(r["t"])
                total += 1
                cts = t.get("created_at_ts")
                claimed = t.get("claimed_at_ts")
                done = t.get("completed_at_ts")
                status = str(t.get("status") or "")
                if cts and claimed:
                    start_latencies.append(max(0.0, (float(claimed) - float(cts)) / 1000.0))
                if cts and done and status in {"DONE", "FAILED", "CANCELLED"}:
                    completion_latencies.append(max(0.0, (float(done) - float(cts)) / 1000.0))
                if status == "DONE":
                    completed += 1
                if status in {"FAILED", "CANCELLED"}:
                    failed += 1

        def _p95(values: List[float]) -> float:
            if not values:
                return 0.0
            vals = sorted(values)
            idx = int(round(0.95 * (len(vals) - 1)))
            return float(vals[idx])

        success_rate = (completed / total) if total else 0.0
        return {
            "window_hours": window_hours,
            "workflow_count": total,
            "completed": completed,
            "failed_or_cancelled": failed,
            "success_rate": round(success_rate, 4),
            "p95_start_latency_s": round(_p95(start_latencies), 2),
            "p95_completion_latency_s": round(_p95(completion_latencies), 2),
            "control": _get_workflow_control(),
        }
    finally:
        neo.close()


@app.post("/api/workflows/control")
def api_workflows_control(body: WorkflowControlIn, user: str = Depends(auth)):
    action = body.action.strip().lower()
    if action not in {"drain", "resume", "set_limits"}:
        raise HTTPException(status_code=400, detail="action must be drain, resume, or set_limits")

    kwargs: dict[str, Any] = {"updated_by": user}
    if action in {"drain", "resume"}:
        kwargs["mode"] = action
    if action == "set_limits":
        if body.max_concurrent_workflows is not None:
            kwargs["max_concurrent_workflows"] = max(1, int(body.max_concurrent_workflows))
        if body.max_batch_backlog is not None:
            kwargs["max_batch_backlog"] = max(1, int(body.max_batch_backlog))
    _set_workflow_control(**kwargs)
    return {"ok": True, "control": _get_workflow_control()}


@app.post("/api/workflows/{workflow_id}/replan")
def api_workflow_replan(workflow_id: str, body: WorkflowReplanIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        workflow_task = neo.get_task(workflow_id)
        if not workflow_task:
            raise HTTPException(status_code=404, detail="Workflow task not found")
        incident_id = neo.create_workflow_incident(
            workflow_id=workflow_id,
            incident_type="replan_requested",
            severity=body.severity,
            detail=body.reason,
            metadata={**(body.metadata or {}), "requested_by": user},
        )
        with neo._session() as s:
            s.run(
                """
                MATCH (t:Task {id:$id})
                SET t.replan_requested=true,
                    t.replan_reason=$reason,
                    t.replan_requested_by=$user,
                    t.replan_requested_at=datetime(),
                    t.replan_requested_at_ts=timestamp(),
                    t.updated_at=datetime(),
                    t.updated_at_ts=timestamp()
                """,
                {"id": workflow_id, "reason": body.reason[:2000], "user": user},
            ).consume()
        return {"workflow_id": workflow_id, "incident_id": incident_id, "replan_requested": True}
    finally:
        neo.close()


@app.post("/api/workflows/{workflow_id}/budget/update")
def api_workflow_budget_update(workflow_id: str, body: WorkflowBudgetUpdateIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        workflow_task = neo.get_task(workflow_id)
        if not workflow_task:
            raise HTTPException(status_code=404, detail="Workflow task not found")
        budget_id = neo.upsert_workflow_budget(
            workflow_id=workflow_id,
            token_budget=body.token_budget,
            time_budget_s=body.time_budget_s,
            retry_budget=body.retry_budget,
            metadata={**(body.metadata or {}), "updated_by": user},
        )
        return {"workflow_id": workflow_id, "budget_id": budget_id, "updated": True}
    finally:
        neo.close()


@app.get("/api/workflows/{workflow_id}/incidents")
def api_workflow_incidents(
    workflow_id: str,
    limit: int = Query(100, ge=1, le=1000),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        items = neo.list_workflow_incidents(workflow_id=workflow_id, limit=limit)
        return {"workflow_id": workflow_id, "items": items, "count": len(items)}
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
        with neo._session() as s:
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
        with neo._session() as s:
            s.run(
                "MATCH (t:Task {id:$id}) SET t.paused=false, t.resumed_at=datetime(), t.resumed_at_ts=timestamp()",
                {"id": task_id},
            )
        return {"task_id": task_id, "paused": False}
    finally:
        neo.close()


@app.post("/api/ask_async")
def api_ask_async(body: AskAsyncIn, user: str = Depends(auth)):
    question = _normalize_ask_question(body.question)
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
            question=question,
            answer_id=answer_id,
            mode="async",
            user=user,
            idempotency_key=body.idempotency_key or answer_id,
        )
    finally:
        neo.close()
    meta = {**(body.meta or {}), **deliverable}
    answers_store.init_answer(answer_id, question, user_meta=meta)
    q = get_q()
    job = q.enqueue(ask_question_job, answer_id, question, body.model, body.max_repairs, deliverable["deliverable_id"])
    answers_store.set_status(answer_id, "QUEUED", job_id=job.get_id())

    if body.idempotency_key:
        idemp_save(body.idempotency_key, {"answer_id": answer_id})

    JOBS_ENQUEUED.inc()
    return {"answer_id": answer_id, "job_id": job.get_id(), "status_url": f"/api/answers/{answer_id}", **deliverable}

@app.post("/api/ask")
def api_ask(body: AskIn, user: str = Depends(auth)):
    question = _normalize_ask_question(body.question)
    mode = (body.mode or "auto").lower()
    if mode not in ("sync", "async", "auto"):
        raise HTTPException(status_code=400, detail="mode must be one of: sync, async, auto")

    if body.idempotency_key and mode == "sync":
        hit = idemp_load(body.idempotency_key)
        if hit and hit.get("sync_result"):
            return hit["sync_result"]

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
                question=question,
                answer_id=None,
                mode="sync",
                user=user,
                idempotency_key=body.idempotency_key,
            )
            out = answer_question(neo, question, model=body.model, max_repairs=body.max_repairs, log_to_neo=True)
            completed = neo.complete_deliverable(
                deliverable_id=deliverable["deliverable_id"],
                status="DONE",
                summary=out.get("answer"),
                result=out,
            )
            out.update(deliverable)
            out["deliverable_status"] = completed.get("status") if completed else "UNKNOWN"
            if body.idempotency_key:
                idemp_save(body.idempotency_key, {"sync_result": out})
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
            question=question,
            answer_id=answer_id,
            mode=mode,
            user=user,
            idempotency_key=body.idempotency_key or answer_id,
        )
    finally:
        neo.close()
    answers_store.init_answer(answer_id, question, user_meta={"mode": mode, **deliverable})
    q = get_q()
    job = q.enqueue(ask_question_job, answer_id, question, body.model, body.max_repairs, deliverable["deliverable_id"])
    answers_store.set_status(answer_id, "QUEUED", job_id=job.get_id())
    JOBS_ENQUEUED.inc()
    if body.idempotency_key:
        idemp_save(body.idempotency_key, {"answer_id": answer_id})

    if mode == "async":
        return JSONResponse(status_code=202, content={"answer_id": answer_id, "job_id": job.get_id(), "status_url": f"/api/answers/{answer_id}", **deliverable})

    # mode == auto: wait budget, else 202
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
async def ws_answer_events(websocket: WebSocket, answer_id: str, token: Optional[str] = Query(None)):
    try:
        _require_ws_auth(token)
    except HTTPException:
        await websocket.close(code=1008)
        return
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
async def ws_answers(websocket: WebSocket, token: Optional[str] = Query(None)):
    try:
        _require_ws_auth(token)
    except HTTPException:
        await websocket.close(code=1008)
        return
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
    request: Request,
    status: str | None = Query(None, description="Optional filter hint; client can also filter"),
    user: str = Depends(auth),
):
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    chan = answers_store._global_chan()
    await pubsub.subscribe(chan)

    async def event_stream():
        # initial hello
        yield _sse("welcome", {"channel": chan})
        last_ping = asyncio.get_event_loop().time()
        try:
            while True:
                if await request.is_disconnected():
                    break
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
                        yield _sse(data.get("type", "update"), data.get("data", {}))

                # keepalive (outside if msg so it fires even when idle)
                if now - last_ping > 15:
                    yield _sse("ping", {"ts": int(now)})
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
async def api_answer_events(answer_id: str, request: Request, user: str = Depends(auth)):
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    chan = answers_store._chan(answer_id)
    await pubsub.subscribe(chan)

    async def stream():
        # initial snapshot so the client shows current status immediately
        snap = answers_store.get_answer(answer_id)
        if snap:
            yield _sse("init", {"status": snap.get("status"), "data": snap.get("data"), "error": snap.get("error")})

        last_ping = asyncio.get_event_loop().time()
        try:
            while True:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                now = asyncio.get_event_loop().time()

                if msg and msg.get("type") == "message":
                    try:
                        payload = json.loads(msg["data"])  # {"type":"new|update","data":{...}}
                    except Exception:
                        payload = {"type": "update", "data": msg["data"]}
                    yield _sse(payload.get("type", "update"), payload.get("data", {}))

                if now - last_ping > 15:
                    yield _sse("ping", {"ts": int(now)})
                    last_ping = now

                await asyncio.sleep(0.2)
        finally:
            try: await pubsub.unsubscribe(chan)
            except Exception: pass
            await pubsub.close()
            await r.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream")

@app.post("/api/llm/stream")
def llm_stream(body: LLMStreamIn, user: str = Depends(auth)):
    """
    Proxies LLM chat with stream=true and re-emits as SSE:
      - 'model'  : the model name
      - 'delta'  : token/partial text chunks (safe: not persisted)
      - 'done'   : final stats (no output text)
      - 'error'  : error info if upstream fails

    Supports both Ollama and OpenAI-compatible (LM Studio) backends
    based on the LLM_BACKEND env var.
    """
    from .llm_client import stream_chat, LLM_MODEL as _default_model

    model = body.model or _default_model
    if not model:
        return StreamingResponse(iter([_sse("error", {"error": "No model configured"})]),
                                 media_type="text/event-stream")

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

    def gen():
        yield _sse("model", {"model": model})
        for ev in stream_chat(msgs, model=model, options=body.options):
            ev_type = ev.get("event", "")
            ev_data = ev.get("data", "")
            yield _sse(ev_type, ev_data)

    return StreamingResponse(gen(), media_type="text/event-stream")
