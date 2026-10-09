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
from .benchmark_controller import BenchmarkController, publish_benchmark_outcome
from .controller_runtime import Neo4jControllerStore
from .loadout_control import LoadoutControlPlane, Neo4jLoadoutStore
from .capacity_forecast import build_capacity_forecast
from .allocation_engine import build_allocation_plan
from .diagnosis_engine import diagnose_incident
from .diagnostic_probes import execute_diagnostic_probes
from .execution_control import ExecutionControlPlane, start_execution_reconciler
from .improvement_cycle import (
    ImprovementCycle,
    build_execution_contract,
    evaluate_completion,
)
from .improvement_runtime import promote_patch
from .harness_views import harness_evolution_snapshot
from .kv_cache import build_manifest
from .recovery_control import (
    Neo4jRecoveryStore,
    RecoveryControlPlane,
    start_recovery_reconciler,
)
from .recovery_runbooks import build_runbook, sign_runbook
from .node_identity import verify_node_token
from .operations_readiness import build_operations_readiness
from .self_healing import SelfHealingController

CONTENT_TYPE_LATEST, generate_latest = load_prometheus_client()
redis = load_redis_module()
aioredis = load_aioredis_module()
Queue = load_queue_class()
from .metrics import QA_REQUESTS, JOBS_ENQUEUED, TASK_CLAIMS, TASK_COMPLETIONS, TASK_HEARTBEATS, CONTEXT_PACKETS
from .metrics import RQ_JOBS_IN_QUEUE, RQ_JOBS_RUNNING, RQ_JOBS_FAILED
from .metrics import REQUESTS
from .metrics import (
    ALLOCATION_RESERVATIONS,
    FLEET_DIAGNOSES,
    KV_CACHE_EVENTS,
    KV_CACHE_PREFILL_MS_SAVED,
    KV_CACHE_RESTORE_MS,
    RECOVERY_OUTCOMES,
    RECOVERY_TRANSITIONS,
)
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
    claim_id: Optional[str] = None

class TaskCompleteIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent_id: str
    status: str = "DONE"
    summary: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    claim_id: Optional[str] = None


class TaskCheckpointIn(BaseModel):
    agent_id: str
    claim_id: str
    checkpoint: Dict[str, Any] = Field(default_factory=dict)
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    estimated_remaining_seconds: Optional[int] = Field(default=None, ge=0)
    pause: bool = False


class TaskPreemptionIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    target_agent_id: Optional[str] = None


class TaskMigrationIn(BaseModel):
    target_agent_id: str = Field(min_length=1)


class ImprovementProposalIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    repository: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=3, max_length=2000)
    allowed_paths: List[str] = Field(min_length=1, max_length=10)
    verification_commands: List[List[str]] = Field(min_length=1, max_length=10)
    recommended_tier: str = "tool-small"
    priority: str = "MEDIUM"
    target_agent_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=300,
    )


class PatchPromotionIn(BaseModel):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=3, max_length=1000)


class BenchmarkControlIn(BaseModel):
    enabled: bool


class LoadoutProposalIn(BaseModel):
    action: Dict[str, Any]


class LoadoutApprovalIn(BaseModel):
    fingerprint: str


class RecoveryProposalIn(BaseModel):
    diagnosis: Dict[str, Any]


class RecoveryApprovalIn(BaseModel):
    fingerprint: str


class RecoveryOutcomeIn(BaseModel):
    verified: bool
    evidence: Dict[str, Any] = Field(default_factory=dict)


class AllocationReservationIn(BaseModel):
    task_id: str
    node_id: str
    model_id: Optional[str] = None
    cache_id: Optional[str] = Field(default=None, max_length=300)
    snapshot_revision: int
    ttl_seconds: int = Field(default=120, ge=30, le=600)


class KVCacheManifestIn(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    cache_id: Optional[str] = None
    prefix_id: str = Field(pattern=r"^prefix-[0-9a-f]{64}$")
    node_id: str = Field(min_length=1, max_length=300)
    endpoint_id: str = Field(min_length=1, max_length=300)
    model_id: str = Field(min_length=1, max_length=500)
    runtime: str = Field(min_length=1, max_length=100)
    compatibility: Dict[str, Any]
    compatibility_fingerprint: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    privacy_scope: str = Field(pattern="^(private|project|fleet)$")
    scope_id: str = Field(min_length=1, max_length=300)
    token_count: int = Field(gt=0)
    bytes: int = Field(default=0, ge=0)
    storage_tier: str = Field(pattern="^(gpu|host|local_disk|distributed)$")
    artifact_ref: Optional[str] = Field(default=None, max_length=1000)
    portable: bool = False
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = Field(default=3600, ge=30, le=604_800)


class KVCacheEventIn(BaseModel):
    cache_id: str = Field(min_length=1, max_length=300)
    node_id: str = Field(min_length=1, max_length=300)
    outcome: str = Field(pattern="^(HIT|MISS|RESTORE|EVICT)$")
    task_id: Optional[str] = Field(default=None, max_length=300)
    prefix_id: Optional[str] = Field(
        default=None,
        pattern=r"^prefix-[0-9a-f]{64}$",
    )
    tokens_saved: int = Field(default=0, ge=0)
    prefill_ms_saved: int = Field(default=0, ge=0)
    restore_ms: int = Field(default=0, ge=0)


class NodeControlIn(BaseModel):
    mode: str = Field(pattern="^(maintenance|quarantined)$")
    reason: str = Field(min_length=1, max_length=500)
    ttl_seconds: int = Field(default=3600, ge=60, le=86_400)


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
    preemptible: bool = False
    max_migrations: int = Field(default=2, ge=0, le=10)


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
        start_recovery_reconciler(Neo4jClient)
    except Exception as e:
        _lifespan_logger.warning(f"Recovery reconciler not started: {e}")
    try:
        start_execution_reconciler(Neo4jClient)
    except Exception as e:
        _lifespan_logger.warning(f"Execution reconciler not started: {e}")
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
    if USER is None or PASS is None:
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
_benchmark_controller = BenchmarkController()
_loadout_control = LoadoutControlPlane()
_self_healing = SelfHealingController()
_recovery_control = RecoveryControlPlane()
_execution_control = ExecutionControlPlane()
_improvement_cycle = ImprovementCycle()

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
              sum(CASE WHEN t.status='RUNNING' THEN 1 ELSE 0 END) AS running,
