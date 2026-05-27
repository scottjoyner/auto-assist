# AssistX Release Architecture

## Overview

AssistX is the authoritative owner of task state and the ingestion bridge from
Sophia into non-realtime execution. For the current cutover release, AssistX
dispatches executable work to the deployed Paperclip service, which invokes
the registered `hermes_local` adapter.

Direct worker claiming and fleet/model-endpoint routing are retained as
follow-up development surfaces. They are not enabled as a substitute for the
Paperclip cutover path.

---

## Data Flow

```
Sophia (voice/auth edge)
    | signed POST /api/voice/events
    v
AssistX (FastAPI + Neo4j, database=assistx)
    | canonical capture / intent / task / dispatch state
    | creates assigned issue and polls result
    v
Paperclip (local user service)
    | adapterType=hermes_local
    v
Hermes execution
    | issue/run/status/output synchronization
    v
AssistX task and artifact outcome
```

---

## Database Split

| Database | Purpose | Contents |
|----------|---------|---------|
| `assistx` | Release integration/control-plane | Sophia captures, tasks, dispatches, events, agent runs, artifacts, policy decisions |
| `neo4j` (legacy/main) | Unified Scott historical memory | Transcripts, summaries, entities, embeddings, long-term facts, preferences, speaker history |
| `memory` | Historical staging only | Not a target for new Sophia cutover data |

Sophia and AssistX must write cutover records to `assistx`; historical memory
lookups may continue to use the legacy memory graph where explicitly required.

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
POST /api/voice/events          — Canonical signed Sophia ingestion endpoint
POST /api/sophia/events         — Compatibility route pending convergence
POST /api/paperclip/events      — Signed Paperclip event callback
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

## Paperclip Execution

For an automatically dispatchable Sophia task:

1. AssistX creates the graph task and dispatch record.
2. AssistX creates an assigned Paperclip issue through its initialized Paperclip client.
3. Paperclip starts the registered `hermes_local` adapter.
4. AssistX polls/captures issue status, active run ID, completion output, and artifacts.
5. A completed run updates the corresponding AssistX task outcome.

The direct task claim endpoints exist for future swarm work; they are not the
supported execution path for this release.

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
