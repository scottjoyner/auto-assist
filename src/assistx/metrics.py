
from __future__ import annotations
from prometheus_client import Counter, Histogram, Gauge

REQUESTS = Counter("assistx_http_requests_total", "HTTP requests", ["path", "method", "status"])
LLM_TOKENS = Counter("assistx_llm_tokens_total", "Estimated LLM tokens", ["model", "mode"])  # mode=text|json
TOOL_CALLS = Counter("assistx_tool_calls_total", "Tool call count", ["tool", "ok"])
TOOL_LATENCY = Histogram("assistx_tool_latency_seconds", "Tool call latency (s)", ["tool"])
EXECUTIONS = Counter("assistx_task_executions_total", "Task executions", ["status"])

QA_REQUESTS = Counter("qa_requests_total", "QA requests", ["mode", "status"])
QA_CYPHER_ATTEMPTS = Counter("qa_cypher_attempts_total", "Cypher attempts")
QA_DURATION = Histogram("qa_duration_seconds", "End-to-end QA duration (s)")

JOBS_ENQUEUED = Counter("rq_jobs_enqueued_total", "Jobs enqueued")
JOBS_STARTED = Counter("rq_jobs_started_total", "Jobs started")
JOBS_SUCCEEDED = Counter("rq_jobs_succeeded_total", "Jobs succeeded")
JOBS_FAILED = Counter("rq_jobs_failed_total", "Jobs failed")