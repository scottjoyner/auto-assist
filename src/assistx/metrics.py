from __future__ import annotations

from prometheus_client import Counter, Histogram, Gauge, REGISTRY


def _safe_counter(name: str, doc: str, labels: list | None = None):
    try:
        return Counter(name, doc, labels or [])
    except ValueError:
        for c in REGISTRY._collector_to_names:
            if hasattr(c, "_name") and c._name == name:
                return c
        return Counter(name, doc, labels or [])


def _safe_gauge(name: str, doc: str, labels: list | None = None):
    try:
        return Gauge(name, doc, labels or [])
    except ValueError:
        for c in REGISTRY._collector_to_names:
            if hasattr(c, "_name") and c._name == name:
                return c
        return Gauge(name, doc, labels or [])


def _safe_histogram(name: str, doc: str, labels: list | None = None):
    try:
        return Histogram(name, doc, labels or [])
    except ValueError:
        for c in REGISTRY._collector_to_names:
            if hasattr(c, "_name") and c._name == name:
                return c
        return Histogram(name, doc, labels or [])


REQUESTS = _safe_counter("assistx_http_requests_total", "HTTP requests", ["path", "method", "status"])
LLM_TOKENS = _safe_counter("assistx_llm_tokens_total", "Estimated LLM tokens", ["model", "mode"])
TOOL_CALLS = _safe_counter("assistx_tool_calls_total", "Tool call count", ["tool", "ok"])
TOOL_LATENCY = _safe_histogram("assistx_tool_latency_seconds", "Tool call latency (s)", ["tool"])
EXECUTIONS = _safe_counter("assistx_task_executions_total", "Task executions", ["status"])

QA_REQUESTS = _safe_counter("qa_requests_total", "QA requests", ["mode", "status"])
QA_CYPHER_ATTEMPTS = _safe_counter("qa_cypher_attempts_total", "Cypher attempts")
QA_DURATION = _safe_histogram("qa_duration_seconds", "End-to-end QA duration (s)")

JOBS_ENQUEUED = _safe_counter("rq_jobs_enqueued_total", "Jobs enqueued")
JOBS_STARTED = _safe_counter("rq_jobs_started_total", "Jobs started")
JOBS_SUCCEEDED = _safe_counter("rq_jobs_succeeded_total", "Jobs succeeded")
JOBS_FAILED = _safe_counter("rq_jobs_failed_total", "Jobs failed")
RQ_JOBS_IN_QUEUE = _safe_gauge("rq_jobs_in_queue", "RQ jobs waiting in the AssistX queue")
RQ_JOBS_RUNNING = _safe_gauge("rq_jobs_running", "RQ jobs currently running in the AssistX queue")
RQ_JOBS_FAILED = _safe_gauge("rq_jobs_failed", "RQ jobs failed in the AssistX queue")

TASK_CLAIMS = _safe_counter("assistx_task_claims_total", "Task trigger claims", ["result"])
TASK_HEARTBEATS = _safe_counter("assistx_task_heartbeats_total", "Task trigger heartbeats", ["status"])
TASK_COMPLETIONS = _safe_counter("assistx_task_completions_total", "Task trigger completions", ["status"])
CONTEXT_PACKETS = _safe_counter("assistx_context_packets_total", "Context packets created")
