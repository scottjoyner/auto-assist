# AssistX Architecture

## Overview

AssistX is the **task-state authority** for an offline swarm of AI agents. It receives events from voice/auth edge (Sophia) and ingestion tools (auto-ingest), owns the authoritative task lifecycle in Neo4j, and makes work available for workers (Hermes, opencode, model endpoints) to claim and execute directly.

No external orchestrator sits between AssistX and workers. Workers connect to Neo4j, claim tasks by calling AssistX REST endpoints, update their own status, and report results through AssistX's event API.

---

## Data Flow

```
Sophia (voice/auth edge)
    |
    | POST /api/events (voice.quick_input.created, voice.auth.decision)
    v
AssistX (FastAPI + Neo4j driver)
    |
    | Creates Task node in assistx DB (status=READY)
    | Records EventEnvelope, Intent, PolicyDecision
    v
Neo4j (assistx database)
    ^
    | Worker finds READY tasks via API or Neo4j query
    | POST /api/tasks/{id}/claim  -> status=CLAIMED, lease set
    | POST /api/tasks/{id}/heartbeat -> lease extended
    | POST /api/tasks/{id}/complete -> status=DONE
    | POST /api/events (agent.run.completed)
    v
Worker (Hermes / opencode / model endpoint)
    |
    | Records AgentRun / ToolCall / Artifact in Neo4j
    v
Neo4j (complete task trace with provenance)
```

---

## Database Split

| Database | Purpose | Contents |
|----------|---------|---------|
| `assistx` | Orchestration / control-plane | Tasks, events, swarm nodes, capabilities, agent runs, tool calls, artifacts, policy decisions, leases |
| `neo4j` (legacy/main) | Unified Scott historical memory | Transcripts, summaries, entities, embeddings, long-term facts, preferences, speaker history |
| `memory` (Sophia) | Transitional voice staging | Voice captures, auth decisions (to be migrated into `neo4j`) |

Workers interact with the `assistx` database for task lifecycle and the `neo4j` database for memory context.

---

## Core Node Types (`assistx` database)

### Task Lifecycle
```
status: READY -> CLAIMED -> RUNNING -> DONE
                  \-> FAILED (retryable -> READY)
                  \-> FAILED (non-retryable -> FAILED)
                  \-> CANCELLED
```

Tasks carry:
- `lease_expires_at_ts` — claim lease, heartbeat extends it
- `approval_required` — set by voice policy for non-Scott speakers
- `risk_level` — low tasks auto-approved for Scott, high always requires approval
- `failure_count`, `error_summary` — for retry tracking

### Event Envelope
All external input arrives via the unified event envelope:
```
event_id, event_type, source_repo, source_service, node_id,
occurred_at, idempotency_key, schema_version, subject, payload,
artifact_refs, privacy
```

### Supported Event Types
```
voice.quick_input.created    - Sophia voice command
voice.auth.decision          - Voice auth result
swarm.node.registered        - Node registration
swarm.node.heartbeat         - Node heartbeat
model.endpoint.discovered    - Model endpoint probe result
ingest.batch.started         - auto-ingest batch start
ingest.memory_candidate.created - auto-ingest candidate
ingest.batch.review_ready    - auto-ingest batch done
```

---

## Voice Authorization Model

| Speaker State | Low-Risk Actions | High-Risk Actions |
|---|---|---|
| `authenticated_scott` | Auto-approve | Requires confirmation |
| `admin_override` | Auto-approve | Requires confirmation |
| `registered_user_authenticated` | Requires Scott approval | Requires Scott approval |
| `unknown_speaker` | Requires Scott approval | Requires Scott approval |

Low-risk actions: create_note, draft_text, search_memory, summarize_context, list_tasks, create_draft_task, classify_file, local_model_analysis

---

## API Endpoints

### Events & Intake
```
POST /api/events                — Receive unified event envelope
POST /api/sophia/events         — Sophia-specific events (legacy)
POST /api/voice/events          — Voice events (legacy)
POST /api/paperclip/events      — Paperclip webhook events (legacy)
```

### Tasks
```
GET  /api/tasks                 — List tasks (filtered)
GET  /api/tasks/{id}            — Get task detail
POST /api/tasks/{id}/claim      — Claim task with optional lease_seconds
POST /api/tasks/{id}/heartbeat  — Extend lease on claimed task
POST /api/tasks/{id}/complete   — Complete task with result
POST /api/tasks/{id}/fail       — Fail task (retryable or terminal)
POST /api/tasks/{id}/cancel     — Cancel task
POST /api/tasks/leases/release-expired — Release all expired leases
GET  /api/tasks/ready           — List READY tasks
```

### Swarm Node Registry
```
POST   /api/swarm/nodes/register           — Register a node
POST   /api/swarm/nodes/{id}/heartbeat     — Node heartbeat
GET    /api/swarm/nodes                    — List nodes
GET    /api/swarm/capabilities             — List all capabilities
```

### Policy
```
GET    /api/policy/voice-action            — Check if action needs approval
```

### Memory
```
GET    /api/memory                         — Memory search
POST   /api/memory/items                   — Store memory item
POST   /api/brain/context                  — Context retrieval
POST   /api/brain/signals                  — Signal processing
```

### Q&A Pipeline
```
POST /api/ask                  — Ask question (sync/async/auto)
GET  /api/answers/{id}         — Get answer
GET  /api/answers/events       — SSE answer stream
WS   /api/answers/{id}/events  — WebSocket answer stream
```

---

## Worker Integration

Workers (Hermes, opencode, model endpoints) integrate directly with AssistX:

1. **Find work**: `GET /api/tasks?status=READY` or query Neo4j directly
2. **Claim**: `POST /api/tasks/{id}/claim` with optional `lease_seconds` (default 900)
3. **Work**: Execute the task using local tools/models
4. **Heartbeat**: `POST /api/tasks/{id}/heartbeat` periodically to extend lease
5. **Complete**: `POST /api/tasks/{id}/complete` with result payload
6. **Report**: `POST /api/events` with `agent.run.completed` event type

Tasks with expired leases are returned to READY via `POST /api/tasks/leases/release-expired` (called by a cron or on-demand).

---

## Neo4j Schema (`assistx` database)

Key constraints:
```
SwarmNode       - node_id UNIQUE
Capability      - capability_id UNIQUE
ModelEndpoint   - model_endpoint_id UNIQUE
Model           - model_id UNIQUE
EventEnvelope   - event_id UNIQUE
Task            - id UNIQUE (legacy)
AgentRun        - id UNIQUE
ToolCall        - id UNIQUE
Artifact        - id UNIQUE
Intent          - id UNIQUE
VoiceAuthDecision - decision_id UNIQUE
PolicyDecision  - decision_id UNIQUE
```

Key relationships:
```
EventEnvelope -[:CREATED_INTENT]-> Intent
EventEnvelope -[:CREATED_TASK]-> Task
EventEnvelope -[:RECORDED_DECISION]-> VoiceAuthDecision
Intent -[:TRIGGERED_TASK]-> Task
Intent -[:AUTHORIZED_BY]-> PolicyDecision
SwarmNode -[:CAN_RUN]-> Capability
SwarmNode -[:EXPOSES]-> ModelEndpoint
ModelEndpoint -[:SERVES]-> Model
Task -[:HAS_RUN]-> AgentRun
AgentRun -[:HAS_TOOL_CALL]-> ToolCall
AgentRun -[:PRODUCED]-> Artifact
```

---

## Deployment

### Docker Compose
```bash
docker compose -f docker-compose.yml -f compose.override.yml up -d
```

See `compose.host.yml` for host-mode Neo4j/Ollama, `compose.override.gpu.yml` for GPU support.

### Environment
Key vars: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE=assistx`, `REDIS_URL`, `BASIC_AUTH_USER`, `BASIC_AUTH_PASS`

### Init
```bash
docker exec -it assistx-api bash -lc "python -m assistx.cli init"
```
