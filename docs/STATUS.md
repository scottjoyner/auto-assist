# AssistX Status — May 26, 2026

## Current State

AssistX is the **task-state authority** for an offline swarm of AI agents. The Phase 2 Offline Swarm MVP is complete and the hardening pass is in progress.

### What Works

- Event envelope intake with idempotency and conflict detection (`POST /api/events`)
- Swarm node registration and heartbeat (`/api/swarm/nodes/*`)
- Task lifecycle: claim, heartbeat, complete, fail, lease release
- Voice policy stub (Scott auto-approve low-risk, all others require approval)
- 8 MVP event types reconciled into Neo4j
- All swarm endpoints wired to the same Basic Auth as legacy API
- Q&A pipeline with sync/async/auto modes and SSE streaming
- Worker queue (RQ) with Redis backend
- Legacy dispatch via Paperclip client

### Database Architecture

| Database | Purpose |
|----------|---------|
| `assistx` | Orchestration state (tasks, events, nodes, runs, artifacts) |
| `neo4j` | Unified Scott historical memory (transcripts, entities, embeddings) |
| `memory` | Sophia transitional staging (to be migrated) |

### What's Next

- Deathstar Hermes skill deploy — blocked on SSH key access
- External producer adoption of outbox client (Sophia, auto-ingest repos)

### Completed (Hardening Pass)

- Swarm schema consolidated into `Neo4jClient.ensure_schema()` — removed duplicate `ensure_swarm_schema()` that ran on every event
- `lease_seconds` parameter on task claim and heartbeat — agents can specify custom lease durations
- Model endpoint probing service — probes `/v1/models` on all registered model endpoints every 5 min via RQ scheduler, emits `model.endpoint.discovered` events
- Local outbox client (`src/assistx/outbox_client.py`) — SQLite-backed queue with HTTP delivery and retry; wired into `/api/events` as fallback; HermesMemoryProvider uses it for resilience; status/flush endpoints at `/api/swarm/outbox/*`
- Test coverage for auth and leases — 3 new tests prove 401 on unauthenticated and custom lease durations work
- Paperclip integration simplified — docstring corrected, unnecessary `paperclip_agent_id` injection removed from task claim path
- Swarm routes fixed — `NameError` on `_default_auth`, duplicate dead code removed, normal `app.include_router()` used

### Running Tests

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_swarm_phase2.py tests/test_migration_api.py -v
```

### Out of Scope

- Sophia runtime audio/STT/TTS changes (Sophia repo)
- Hermes agent code changes (hermes-agent repo)
- UI dashboard redesign (templates already exist)
- Paperclip deprecation (optional mirror remains)
