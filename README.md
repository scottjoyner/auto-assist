# AssistX — Transcripts → Tasks → Executions (Neo4j + Ollama)


## Quick Wins Implemented
- JSON validation & retries for extraction
- Prompt+response caching (SQLite)
- Task review queue (`REVIEW → READY`)
- Policy gating for tools
- Structured logs with run/task IDs


## Run with Docker Compose
```bash
# Set passwords via env or export NEO4J_PASSWORD=mysecret
docker compose up --build
# Then open the review UI: http://localhost:8000
# Neo4j Browser: http://localhost:7474
# Ollama: http://localhost:11434
```
### First-time model pull inside the ollama container
```bash
docker exec -it assistx-ollama ollama pull ${OLLAMA_MODEL:-llama3.1:8b}
```
### Initialize schema (from API container shell)
```bash
docker exec -it assistx-api bash -lc "python -m assistx.cli init"
```

## Review UI (FastAPI)
- `/` — home
- `/tasks/review` — approve tasks (REVIEW → READY)
- `/tasks/ready` — tasks queued for execution
- `/runs` — recent agent runs



### Basic Auth for the dashboard
Set credentials (defaults `admin`/`admin`):
```bash
export BASIC_AUTH_USER=admin
export BASIC_AUTH_PASS=change-me
```
They are required for all UI endpoints.


## Prometheus metrics
The API exposes Prometheus metrics at `GET /metrics` (Basic Auth protected). Common series:
- `assistx_http_requests_total{path,method,status}`
- `assistx_llm_tokens_total{model,mode}`
- `assistx_tool_calls_total{tool,ok}` and `assistx_tool_latency_seconds_bucket`
- `assistx_task_executions_total{status}`

Scrape example:
```yaml
scrape_configs:
  - job_name: 'assistx'
    basic_auth:
      username: ${BASIC_AUTH_USER}
      password: ${BASIC_AUTH_PASS}
    static_configs: [{ targets: ['localhost:8000'] }]
    metrics_path: /metrics
```

## Background queue
A Redis + RQ worker processes task executions off the web thread.

Queue a task:
```bash
curl -u $BASIC_AUTH_USER:$BASIC_AUTH_PASS -X POST http://localhost:8000/tasks/<task_id>/enqueue
```
Worker logs will show progress; results and steps are written to Neo4j.

## Token accounting
Ollama does not currently return token counts. We record **estimated** tokens for prompts and tool IO. If you switch to a model/server that reports token usage, you can:
- increment `LLM_TOKENS` with real counts in `ollama_llm.py` hooks, or
- adapt `json_chat/text_chat` to parse usage from responses and call `LLM_TOKENS.labels(...).inc(actual)`.

