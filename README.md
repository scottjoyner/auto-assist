# AssistX — Multi-Agent Orchestration Platform (Neo4j + Paperclip + Hermes + Offline Swarm)

Command center for cross-device agent work with Neo4j graph memory,
Paperclip assignment hub, Hermes agent sessions, and the emerging offline
Tailscale-first swarm compute architecture.

Key integrations:

- **Neo4j**: orchestration state, knowledge graph, memory, provenance, swarm registry
- **AssistX Task Authority**: task lifecycle, claims, heartbeats, failures, leases, approvals
- **Swarm Events**: unified event envelope for Sophia, auto-ingest, model endpoints, and nodes
- **Paperclip**: optional dispatch / delegation mirror, not the source of task truth
- **Hermes / OpenAI-compatible endpoints**: local agent/model execution targets
- **Voice/TTS**: intent capture via browser/media recording; Sophia runtime integration pending

Legacy pipeline (transcripts → summaries → tasks → executions) remains active.

---

## Current North Star

AssistX is becoming the offline swarm control plane:

```text
Sophia voice/auth edge
        -> AssistX task authority + policy + dispatch
        -> local swarm node / Hermes / opencode / model endpoint
        -> AssistX records AgentRun / ToolCall / Artifact / Memory event
        -> Sophia or UI returns the response
```

Long-term memory about Scott should live in the main unified Neo4j memory graph. AssistX owns orchestration state. auto-ingest periodically enriches historical memory and should not block the real-time voice/task path.

See [`UNIFICATION.md`](UNIFICATION.md) for the cross-repo plan shared with `Sophia` and `auto-ingest`.

---

## Offline Swarm Phase 2 MVP Status

The Phase 2 swarm MVP has been added on top of the existing AssistX stack.

New docs:

- [`docs/HANDOFF_PHASE_2_SWARM_MVP.md`](docs/HANDOFF_PHASE_2_SWARM_MVP.md) — current implementation state, risks, and next-agent prompt
- [`docs/swarm_contracts/`](docs/swarm_contracts/) — concrete contracts for DB unification, task authority, node registry, event envelope, voice auth policy, artifacts, model endpoints, and auto-ingest enrichment

New implementation files:

```text
src/assistx/swarm_core.py
src/assistx/swarm_routes.py
deploy/swarm_nodes.example.json
tests/test_swarm_phase2.py
```

New API endpoints:

```text
POST /api/events
POST /api/swarm/nodes/register
POST /api/swarm/nodes/{node_id}/heartbeat
GET  /api/swarm/nodes
GET  /api/swarm/capabilities
POST /api/tasks/{task_id}/fail
POST /api/tasks/leases/release-expired
GET  /api/policy/voice-action
```

Implemented MVP capabilities:

- event envelope validation and idempotent replay
- payload hash conflict detection
- graph reconciliation for core Sophia, auto-ingest, swarm node, and model endpoint events
- swarm node and capability registry
- task failure endpoint
- task lease timestamps on claim/heartbeat
- expired lease release back to `READY`
- voice policy stub for low-risk Scott auto-approval and unknown-speaker approval gating

Important limitation: the new swarm router is currently attached through a bootstrap shim in `rate_limiter.py` to avoid replacing the large legacy `api.py`. The next pass should move it to a normal `app.include_router()` call and wire the new routes into the same auth dependency as the rest of the API.

---

## Run (Compose)

See `docker-compose.yml`. Once up:

1. Pull your model inside the ollama container:

   ```bash
   docker exec -it assistx-ollama ollama pull ${OLLAMA_MODEL:-llama3.1:8b}
   ```

2. Init Neo4j schema:

   ```bash
   docker exec -it assistx-api bash -lc "python -m assistx.cli init"
   ```

3. Ensure Paperclip server is running if using Paperclip dispatch:

   ```bash
   systemctl --user status paperclip
   ```

Dispatch API:

```bash
curl -u admin:change-me http://localhost:8000/api/dispatches
```

Review UI: http://localhost:8000 (Basic Auth)

Metrics: http://localhost:8000/metrics (Basic Auth)

---

## Swarm API Smoke Examples

Register a node:

```bash
curl -X POST http://localhost:8000/api/swarm/nodes/register \
  -H 'Content-Type: application/json' \
  -d '{
    "node_id":"demo-1",
    "hostname":"demo-1",
    "status":"online",
    "roles":["fast_delegation_agent","model_endpoint"],
    "capabilities":[{"capability_id":"demo-1.llm.fast","kind":"llm","name":"Fast local generation"}]
  }'
```

Submit a unified event envelope:

```bash
curl -X POST http://localhost:8000/api/events \
  -H 'Content-Type: application/json' \
  -d '{
    "event_id":"voice-demo-1",
    "event_type":"voice.quick_input.created",
    "source_repo":"Sophia",
    "source_service":"voice-agent",
    "node_id":"x1-370",
    "occurred_at":"2026-05-26T18:00:00-04:00",
    "idempotency_key":"voice-demo-1",
    "schema_version":"1.0",
    "subject":{"kind":"utterance","id":"utterance-demo-1"},
    "payload":{"text":"Create a low risk draft note.","auth_state":"authenticated_scott","action":"create_draft_task","risk_level":"low"},
    "artifact_refs":[],
    "privacy":{"pii":true,"privacy_class":"private","retention_class":"keep"}
  }'
```

Until the next hardening pass wires these routes into legacy auth, treat them as Tailscale/LAN-only trusted-network endpoints.

---

## Tests

Run the new swarm tests and existing migration API tests:

```bash
python -m pytest tests/test_swarm_phase2.py -v
python -m pytest tests/test_migration_api.py -v
```

The tests use the existing Dockerized Neo4j fixture.

---

## Go-Live Checklist (Existing Phase 6)

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

---

## Go-Live Commands (Copy/Paste)

```bash
set -a; source .env; set +a
docker compose up -d api worker
systemctl --user restart hermes-agent-adapter.service

BASE_URL=http://localhost:8000 src/scripts/phase6_preflight.sh
BASE_URL=http://localhost:8000 src/scripts/phase6_callback_smoke.sh
BASE_URL=http://localhost:8000 src/scripts/phase6_canary_gate.sh
```

---

## CLI

```bash
python -m assistx.cli ingest --src ./transcripts
python -m assistx.cli summarize --since-days 7
python -m assistx.cli approve --all
python -m assistx.cli execute --limit 5
python -m assistx.cli export-pred --out ./eval/pred
python -m assistx.cli eval --gold ./eval/gold --pred ./eval/pred
```

---

## How to Use

### All-in-Docker (Compose runs Neo4j + Ollama)

```bash
docker compose -f docker-compose.yml -f compose.infra.yml config
docker compose -f docker-compose.yml -f compose.infra.yml up -d
```

### Use host Neo4j + Ollama

```bash
docker compose -f docker-compose.yml -f compose.host.yml config
docker compose -f docker-compose.yml -f compose.host.yml up -d
```

### Sanity Help

```bash
# stop whatever is running
docker compose -f docker-compose.yml -f compose.override.yml --profile infra stop

# validate config after edits
docker compose -f docker-compose.yml -f compose.override.yml config

# bring up with host services
docker compose -f docker-compose.yml -f compose.override.yml up -d
```

### Streamlit

```bash
BASIC_AUTH_USER=admin BASIC_AUTH_PASS=change-me streamlit run streamlit_app.py
```

### Bring it up clean

```bash
docker compose -f docker-compose.yml -f compose.host.yml down --remove-orphans
docker compose -f docker-compose.yml -f compose.host.yml build --no-cache
docker compose -f docker-compose.yml -f compose.host.yml up -d
docker compose -f docker-compose.yml -f compose.host.yml ps
docker logs -n 100 assistx-api
curl -fsS http://localhost:8000/health
```

---

## Documentation

See [docs/INDEX.md](docs/INDEX.md) for the complete implementation package index.

Key docs:

- [Shared Unification Plan](UNIFICATION.md)
- [Phase 2 Swarm MVP Handoff](docs/HANDOFF_PHASE_2_SWARM_MVP.md)
- [Swarm Contracts](docs/swarm_contracts/)
- [Migration Plan](MIGRATION.md)
- [Implementation Guide](docs/IMPLEMENTATION_GUIDE.md)
- [Phase 3: Paperclip Integration](docs/PHASE_3_PAPERCLIP_INTEGRATION.md)
- [Execution Summary](docs/EXECUTION_SUMMARY.md)
