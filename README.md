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

