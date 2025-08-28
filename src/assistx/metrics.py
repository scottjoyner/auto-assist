
from __future__ import annotations
from prometheus_client import Counter, Histogram

REQUESTS = Counter("assistx_http_requests_total", "HTTP requests", ["path", "method", "status"])
LLM_TOKENS = Counter("assistx_llm_tokens_total", "Estimated LLM tokens", ["model", "mode"])  # mode=text|json
TOOL_CALLS = Counter("assistx_tool_calls_total", "Tool call count", ["tool", "ok"])
TOOL_LATENCY = Histogram("assistx_tool_latency_seconds", "Tool call latency (s)", ["tool"])
EXECUTIONS = Counter("assistx_task_executions_total", "Task executions", ["status"])
