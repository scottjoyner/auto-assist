# AssistX - Complete Implementation Package

## Overview

This directory contains the implementation package for the AssistX stack: Hermes + Neo4j + Paperclip + Voice/Media + Production Hardening + the emerging Offline Swarm Compute control plane.

AssistX is now being aligned with `Sophia` and `auto-ingest` through the shared unification plan in `../UNIFICATION.md`.

Current high-level role split:

```text
AssistX = task authority, orchestration state, approvals, dispatch, swarm event intake
Sophia = voice/auth edge and spoken response layer
auto-ingest = periodic historical memory enrichment
Neo4j = graph state, memory, provenance, task and event records
Paperclip = optional delegation/human coordination mirror
```

---

## Read First

For a new engineer or agent, read these first:

1. [`../UNIFICATION.md`](../UNIFICATION.md) — shared cross-repo unification plan
2. [`HANDOFF_PHASE_2_SWARM_MVP.md`](HANDOFF_PHASE_2_SWARM_MVP.md) — current swarm MVP state and next-agent prompt
3. [`swarm_contracts/`](swarm_contracts/) — concrete contracts for the swarm architecture
4. [`IMPLEMENTATION_GUIDE.md`](IMPLEMENTATION_GUIDE.md) — legacy AssistX/Hermes/Paperclip implementation guide
5. [`EXECUTION_SUMMARY.md`](EXECUTION_SUMMARY.md) — historical build summary

---

## Offline Swarm Phase 2 MVP

Status: implemented as an MVP, with hardening follow-up required.

Primary files:

```text
src/assistx/swarm_core.py
src/assistx/swarm_routes.py
src/assistx/rate_limiter.py
tests/test_swarm_phase2.py
deploy/swarm_nodes.example.json
```

Primary handoff:

- [`HANDOFF_PHASE_2_SWARM_MVP.md`](HANDOFF_PHASE_2_SWARM_MVP.md)

Contract docs:

- [`swarm_contracts/database_unification.md`](swarm_contracts/database_unification.md)
- [`swarm_contracts/task_authority.md`](swarm_contracts/task_authority.md)
- [`swarm_contracts/event_envelope.md`](swarm_contracts/event_envelope.md)
- [`swarm_contracts/node_registry.md`](swarm_contracts/node_registry.md)
- [`swarm_contracts/model_endpoint_registry.md`](swarm_contracts/model_endpoint_registry.md)
- [`swarm_contracts/voice_auth_policy.md`](swarm_contracts/voice_auth_policy.md)
- [`swarm_contracts/artifact_paths.md`](swarm_contracts/artifact_paths.md)
- [`swarm_contracts/auto_ingest_memory_enrichment.md`](swarm_contracts/auto_ingest_memory_enrichment.md)

Implemented endpoints:

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

Implemented behavior:

- event envelope validation
- idempotent replay and conflict detection
- graph reconciliation for MVP event types
- swarm node/capability registration
- model endpoint graph records from events
- task failure endpoint
- task lease timestamps on claim/heartbeat
- expired lease release back to `READY`
- voice policy stub for Scott low-risk auto-approval and unknown-speaker approval gating

Known hardening requirement:

- new swarm routes should be moved from bootstrap shim to normal `app.include_router()` in `api.py`
- swarm routes should use the same auth dependency as legacy API routes
- swarm schema patch should be moved directly into `Neo4jClient.ensure_schema()`

---

## Existing AssistX Implementation Phases

### Phase 0: Inventory & Contract

Document: [`PHASE_0_INVENTORY.md`](PHASE_0_INVENTORY.md)

Contains:

- Service inventory: Neo4j, Redis, Paperclip, Hermes
- Port mapping and connectivity
- Credentials and environment variables
- Neo4j schema documentation
- Sample API payloads
- Implementation epics and timeline

### Phase 1: Brain Schema & Retrieval

Primary file: `src/assistx/neo4j_client.py`

Implemented:

- Intent creation with idempotency
- Context packet creation
- Context retrieval with source references
- Bounded result size limiting
- Graph-first task trigger lifecycle
- Ticket hierarchy on `Task` nodes

Endpoints:

```text
POST /api/intents
GET  /api/intents
GET  /api/intents/{intent_id}
POST /api/brain/context
GET  /api/context-packets/{packet_id}
POST /api/ask
POST /api/ask_async
GET  /api/agent/tasks
POST /api/tasks/{task_id}/claim
POST /api/tasks/{task_id}/heartbeat
POST /api/tasks/{task_id}/complete
POST /api/tickets
GET  /api/tickets/{ticket_id}/tree
```

### Phase 2: Hermes Memory Integration

Document: [`PHASE_2_HERMES_MEMORY_INTEGRATION.md`](PHASE_2_HERMES_MEMORY_INTEGRATION.md)

Primary file: `src/assistx/agents/hermes_memory_provider.py`

Implemented:

- `HermesMemoryProvider`
- `prefetch()`
- `write_memory()`
- `signal_event()`
- `update_session()`
- Tool definitions for graph context search and graph memory write

Endpoints:

```text
POST /api/memory/items
GET  /api/memory
GET  /api/memory/{memory_id}
POST /api/brain/signals
POST /api/sessions/{session_id}
GET  /api/sessions
```

### Phase 3: Paperclip Dispatch Integration

Document: [`PHASE_3_PAPERCLIP_INTEGRATION.md`](PHASE_3_PAPERCLIP_INTEGRATION.md)

Primary file: `src/assistx/paperclip_client.py`

Implemented:

- `PaperclipClient`
- optional Paperclip issue creation from `/api/dispatch`
- local Neo4j dispatch records
- event ingestion endpoint
- fallback when Paperclip is unavailable

Clarification:

```text
AssistX Task = source of truth
Paperclip Issue = optional mirror / delegation wrapper
```

Endpoints:

```text
POST /api/dispatch
GET  /api/dispatches
POST /api/paperclip/events
POST /api/devices/{device_id}
GET  /api/devices
GET  /api/devices/{device_id}
```

### Phase 4: Command Center UI

Primary file: `src/assistx/api.py`

Implemented:

- intent views
- device views
- session views
- memory browsing
- task control routes
- dispatch controls
- review queue routes

### Phase 5: Voice/Video Intake

Partially implemented:

- `/ingest` page with browser audio/video recording
- `POST /api/captures`
- media and transcript graph intake records

Pending:

- Sophia runtime voice integration
- voiceprint auth
- unknown speaker registration runtime
- TTS response integration
- barge-in/cancellation runtime integration

### Phase 6: Hardening and Rollout

Documents:

- [`PHASE_6_HARDENING_ROLLOUT.md`](PHASE_6_HARDENING_ROLLOUT.md)
- [`CANARY_ACCEPTANCE_2026-05-24.md`](CANARY_ACCEPTANCE_2026-05-24.md)

Existing scope:

- secrets
- monitoring
- canary strategy
- rollback procedures

Swarm-specific hardening is now tracked in [`HANDOFF_PHASE_2_SWARM_MVP.md`](HANDOFF_PHASE_2_SWARM_MVP.md).

---

## File Structure

```text
auto-assist/
├── UNIFICATION.md
├── src/assistx/
│   ├── api.py
│   ├── neo4j_client.py
│   ├── swarm_core.py
│   ├── swarm_routes.py
│   ├── rate_limiter.py
│   ├── paperclip_client.py
│   └── agents/
│       └── hermes_memory_provider.py
├── tests/
│   ├── test_migration_api.py
│   ├── test_swarm_phase2.py
│   └── test_hermes_memory_provider.py
├── deploy/
│   └── swarm_nodes.example.json
└── docs/
    ├── HANDOFF_PHASE_2_SWARM_MVP.md
    ├── swarm_contracts/
    ├── PHASE_0_INVENTORY.md
    ├── PHASE_2_HERMES_MEMORY_INTEGRATION.md
    ├── PHASE_3_PAPERCLIP_INTEGRATION.md
    ├── IMPLEMENTATION_GUIDE.md
    ├── EXECUTION_SUMMARY.md
    └── INDEX.md
```

---

## Configuration Reference

Sample environment variables:

```bash
# Neo4j
NEO4J_URI=bolt://host.docker.internal:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<set locally>
NEO4J_DATABASE=assistx

# LLM Backend
LLM_BACKEND=openai
OPENAI_BASE_URL=http://host.docker.internal:1234/v1
OPENAI_API_KEY=not-needed
LLM_MODEL=llama3.1:8b
EMBED_MODEL=nomic-embed-text

# AssistX API
BASIC_AUTH_USER=admin
BASIC_AUTH_PASS=change-me

# Redis
REDIS_URL=redis://redis:6379/0

# Paperclip
PAPERCLIP_API_URL=http://host.docker.internal:3100/api
PAPERCLIP_API_TOKEN=<token-from-paperclip-agent-keys>
PAPERCLIP_WORKSPACE_ID=<paperclip-company-uuid>
PAPERCLIP_WEBHOOK_SECRET=<set-locally>

# Voice webhook
VOICE_WEBHOOK_SECRET=<set-locally>
WS_AUTH_TOKEN=<set-locally>
```

Do not commit real secrets.

---

## Test Commands

```bash
python -m pytest tests/test_swarm_phase2.py -v
python -m pytest tests/test_migration_api.py -v
python -m pytest tests/test_hermes_memory_provider.py -v
```

The integration tests use the existing Dockerized Neo4j fixture.

---

## Next Handoff Prompt

See the full prompt in [`HANDOFF_PHASE_2_SWARM_MVP.md`](HANDOFF_PHASE_2_SWARM_MVP.md). The short version:

```text
Harden the Phase 2 Offline Swarm MVP:
- replace bootstrap router patch with direct app.include_router()
- move schema extension directly into Neo4jClient.ensure_schema()
- put swarm routes behind existing auth behavior
- add lease_seconds support to task claim
- add model endpoint probe service
- add local outbox client for Sophia and auto-ingest producers
- expand tests for auth, lease duration, and model discovery
```
