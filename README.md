
# AssistX — Transcripts → Summaries → Tasks → Executions (Neo4j + Ollama)

This repository contains a local-first, production-minded pipeline:
- Ingest transcripts
- Summarize + extract tasks (Ollama)
- Store in Neo4j
- Execute tasks with a tool-using agent
- Log provenance (ToolCalls, Artifacts), metrics, and acceptance checks

## Run (Compose)
See `docker-compose.yml`. Once up:
1) Pull your model inside the ollama container:
   `docker exec -it assistx-ollama ollama pull ${OLLAMA_MODEL:-llama3.1:8b}`
2) Init Neo4j schema:
   `docker exec -it assistx-api bash -lc "python -m assistx.cli init"`

Review UI: http://localhost:8000 (Basic Auth)  
Metrics: http://localhost:8000/metrics (Basic Auth)

## CLI
```bash
python -m assistx.cli ingest --src ./transcripts
python -m assistx.cli summarize --since-days 7
python -m assistx.cli approve --all
python -m assistx.cli execute --limit 5
# Export predictions for evaluator
python -m assistx.cli export-pred --out ./eval/pred
python -m assistx.cli eval --gold ./eval/gold --pred ./eval/pred
```


How to use
### All-in-Docker (Compose runs Neo4j + Ollama)
```
docker compose -f docker-compose.yml -f compose.infra.yml config
docker compose -f docker-compose.yml -f compose.infra.yml up -d
```
### Use host Neo4j + Ollama
```
docker compose -f docker-compose.yml -f compose.host.yml config
docker compose -f docker-compose.yml -f compose.host.yml up -d
```

### Sanity Help
```
# stop whatever’s running
docker compose -f docker-compose.yml -f compose.override.yml --profile infra stop

# validate config after your edits
docker compose -f docker-compose.yml -f compose.override.yml config

# bring up with host services (no infra)
docker compose -f docker-compose.yml -f compose.override.yml up -d
```

### HOW TO RUN STREAMLIT
```
BASIC_AUTH_USER=admin BASIC_AUTH_PASS=change-me streamlit run streamlit_app.py
```