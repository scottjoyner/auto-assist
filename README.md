
# AssistX — Multi-Agent Orchestration Platform (Neo4j + Paperclip + Hermes)

Command center for cross-device agent work with Neo4j graph memory,
Paperclip assignment hub, and Hermes agent sessions.

Key integrations:
- **Paperclip** (live): dispatch tasks → create issues → route to agents
- **Neo4j**: orchestration state, knowledge graph, memory, provenance
- **Hermes**: persistent agent sessions with external memory provider
- **Voice/TTS**: intent capture via browser media recording

Legacy pipeline (transcripts → summaries → tasks → executions) still active.

## Run (Compose)
See `docker-compose.yml`. Once up:
1) Pull your model inside the ollama container:
   `docker exec -it assistx-ollama ollama pull ${OLLAMA_MODEL:-llama3.1:8b}`
2) Init Neo4j schema:
   `docker exec -it assistx-api bash -lc "python -m assistx.cli init"`
3) Ensure Paperclip server is running (see [docs/PHASE_3_PAPERCLIP_INTEGRATION.md](docs/PHASE_3_PAPERCLIP_INTEGRATION.md)):
   `systemctl --user status paperclip`

Dispatch API:
```
curl -u admin:change-me http://localhost:8000/api/dispatches
```

Review UI: http://localhost:8000 (Basic Auth)  
Metrics: http://localhost:8000/metrics (Basic Auth)

## Go-Live Checklist (Phase 6)

1. Set required secrets in `.env`:
   - `PAPERCLIP_API_TOKEN`
   - `PAPERCLIP_WEBHOOK_SECRET`
   - `VOICE_WEBHOOK_SECRET`
   - `WS_AUTH_TOKEN`
2. Restart API/worker with current env:
   - `docker compose up -d api worker`
3. Ensure host Hermes adapter is running:
   - `systemctl --user status hermes-agent-adapter.service`
4. Run rollout checks:
   - `src/scripts/phase6_preflight.sh`
   - `src/scripts/phase6_callback_smoke.sh`
   - `src/scripts/phase6_canary_gate.sh`
5. Run a live canary task and verify `READY -> RUNNING -> DONE`.

See [docs/PHASE_6_HARDENING_ROLLOUT.md](docs/PHASE_6_HARDENING_ROLLOUT.md) and
[docs/CANARY_ACCEPTANCE_2026-05-24.md](docs/CANARY_ACCEPTANCE_2026-05-24.md).

## Go-Live Commands (Copy/Paste)

```bash
set -a; source .env; set +a
docker compose up -d api worker
systemctl --user restart hermes-agent-adapter.service

BASE_URL=http://localhost:8000 src/scripts/phase6_preflight.sh
BASE_URL=http://localhost:8000 src/scripts/phase6_callback_smoke.sh
BASE_URL=http://localhost:8000 src/scripts/phase6_canary_gate.sh
```

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


```
# Rebuild and up
docker compose -f docker-compose.yml -f compose.infra.yml up --build -d
# or with host mode: -f compose.host.yml

# Hit the dashboard (Basic Auth protected)
open http://localhost:8000/answers

# In another terminal, enqueue a question to see live "new"/"update"
curl -u admin:change-me -X POST -H "Content-Type: application/json" \
  -d '{"question":"What tasks are READY by kind?","mode":"async"}' \
  http://localhost:8000/api/ask
```
### Bring it up clean
```
# stop current stack
docker compose -f docker-compose.yml -f compose.host.yml down --remove-orphans

# rebuild with the new Dockerfile and deps
docker compose -f docker-compose.yml -f compose.host.yml build --no-cache

# start
docker compose -f docker-compose.yml -f compose.host.yml up -d

# verify containers (api & worker should appear, not just redis)
docker compose -f docker-compose.yml -f compose.host.yml ps

# check logs for the API
docker logs -n 100 assistx-api

# health
curl -fsS http://localhost:8000/health
```

```
docker compose -f docker-compose.yml -f compose.host.yml down --remove-orphans
docker compose -f docker-compose.yml -f compose.host.yml build --no-cache api worker
docker compose -f docker-compose.yml -f compose.host.yml up -d
```

## Documentation

See [docs/INDEX.md](docs/INDEX.md) for the complete implementation package index.
- [Migration Plan](MIGRATION.md) — full architecture and phased plan
- [Implementation Guide](docs/IMPLEMENTATION_GUIDE.md) — setup and API reference
- [Phase 3: Paperclip Integration](docs/PHASE_3_PAPERCLIP_INTEGRATION.md) — dispatch setup
- [Execution Summary](docs/EXECUTION_SUMMARY.md) — what's built and tested
