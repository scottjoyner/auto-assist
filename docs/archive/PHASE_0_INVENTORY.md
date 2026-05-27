# Phase 0 - Inventory and Contract Freeze

## Executive Summary

This document captures the baseline infrastructure, services, ports, credentials, and implementation contracts for the AssistX + Hermes + Neo4j + Paperclip migration.

---

## 1. Service Inventory

### Core Services

| Service | Port | Status | Notes |
|---------|------|--------|-------|
| AssistX API | 8000 | ✅ Running | FastAPI on Uvicorn, Neo4j-backed |
| Neo4j | 7687 | ✅ Configured | Multi-database capable, 5.23.0+ |
| Redis | 6379 | ✅ Configured | For RQ queues and caching |
| Ollama (Optional) | 11434 | ⚠️ Optional | For local LLM inference |
| Paperclip | 3100 | ✅ Running | systemd user service on host, `local_trusted` mode, embedded PostgreSQL |
| Hermes Agent (Local) | TBD | ⚠️ External | CLI-based via hermes-paperclip-adapter |
| Hermes Agent (Remote) | TBD | ⚠️ External | Multiple devices, coordinated via Paperclip |

### Current AssistX Endpoints

```
GET  /                              # Home page
GET  /health                        # Health check
GET  /ingest                        # Audio upload UI
POST /upload-audio                  # Transcription ingestion
GET  /api/transcriptions            # List transcriptions
GET  /api/transcriptions/{tid}      # Get transcription + tasks
POST /api/transcriptions/{tid}/task # Create task from transcription
POST /api/transcriptions/{tid}/embed # Enqueue embedding job

GET  /api/tasks                     # List tasks
GET  /api/tasks/{task_id}           # Get task details
POST /api/ask                       # Sync question answering
POST /api/ask_async                 # Async question answering
GET  /api/answers                   # List answers
GET  /api/answers/{answer_id}       # Get answer

# New Brain APIs (Phase 1-4)
POST /api/intents                   # Create intent
GET  /api/intents                   # List intents
GET  /api/intents/{intent_id}       # Get intent

POST /api/brain/context             # Create context packet
GET  /api/context-packets/{packet_id} # Get context packet

POST /api/dispatch                  # Create dispatch
GET  /api/dispatches                # List dispatches
POST /api/dispatches/{dispatch_id}/reassign # Reassign dispatch
POST /api/paperclip/events          # Ingest Paperclip events

# Graph-first Task trigger APIs
GET  /api/agent/tasks               # Agent polls READY tasks by capability
POST /api/tasks/{task_id}/claim     # Atomically claim a READY task
POST /api/tasks/{task_id}/heartbeat # Update active task heartbeat/progress
POST /api/tasks/{task_id}/complete  # Complete task and write AgentRun/outcome
POST /api/tickets                   # Create epic/story/task ticket
GET  /api/tickets/{ticket_id}/tree  # Inspect epic/story/task hierarchy
POST /api/ask                       # Creates ask deliverable + answer response
POST /api/ask_async                 # Creates ask deliverable + async completion event

POST /api/sessions/{session_id}     # Update agent session
GET  /api/sessions                  # List sessions

POST /api/memory/items              # Write memory
GET  /api/memory                    # List memory items
GET  /api/memory/{memory_id}        # Get memory item

POST /api/brain/signals             # Create signal event

GET  /api/devices                   # List agent devices
GET  /api/devices/{device_id}       # Get device details

# Task control endpoints
POST /api/tasks/{task_id}/cancel    # Cancel task
POST /api/tasks/{task_id}/pause     # Pause task
POST /api/tasks/{task_id}/resume    # Resume task
```

---

## 2. Neo4j Database Configuration

### Current Setup

- **Version**: 5.24-enterprise (host container), 5.22+ (test ephemeral)
- **Auth**: Basic (`NEO4J_USER`/`NEO4J_PASSWORD`)
- **URI**: `NEO4J_URI` (default: `bolt://host.docker.internal:7687`)
- **Database**: `NEO4J_DATABASE=assistx` — dedicated database on the host enterprise instance

### Database Strategy

**Decision: Dedicated Database (Enterprise)**

We create a dedicated `assistx` database on the existing host `neo4j:5.24-enterprise` container (not a compose-managed service). This leverages the host's Enterprise Edition for multi-database support, giving clear data isolation vs. the label-namespacing approach in the original `neo4j` database.

The `assistx` database is created via:
```cypher
CREATE DATABASE assistx IF NOT EXISTS
```

All AssistX graph operations (constraints, indexes, CRUD) target this database.

**Label Categories**

- `v1` nodes: Conversation, Utterance, Summary, Task, AgentRun, ToolCall, Artifact
- `v2` nodes: Transcription, Segment
- `Orchestration` nodes: Intent, ContextPacket, Dispatch, AgentSession, AgentDevice, MemoryItem, SignalEvent

### Constraints and Indexes

Automatically created on startup via `neo.ensure_schema()`:

**Uniqueness Constraints**
```cypher
CREATE CONSTRAINT IF NOT EXISTS FOR (c:Conversation) REQUIRE c.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (u:Utterance)   REQUIRE u.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (s:Summary)     REQUIRE s.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (t:Task)        REQUIRE t.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (r:AgentRun)    REQUIRE r.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (k:ToolCall)    REQUIRE k.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (a:Artifact)    REQUIRE a.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (tr:Transcription) REQUIRE tr.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (sg:Segment)       REQUIRE sg.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (i:Intent)       REQUIRE i.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (p:ContextPacket) REQUIRE p.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (d:Dispatch)     REQUIRE d.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (s:AgentSession)  REQUIRE s.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (v:AgentDevice)   REQUIRE v.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (m:MemoryItem)    REQUIRE m.id IS UNIQUE
CREATE CONSTRAINT IF NOT EXISTS FOR (e:SignalEvent)   REQUIRE e.id IS UNIQUE
```

**Performance Indexes**
```cypher
CREATE INDEX IF NOT EXISTS FOR (t:Task)            ON (t.status)
CREATE INDEX IF NOT EXISTS FOR (t:Task)            ON (t.kind)
CREATE INDEX IF NOT EXISTS FOR (t:Task)            ON (t.created_at_ts)
CREATE INDEX IF NOT EXISTS FOR (tr:Transcription)  ON (tr.key)
CREATE INDEX IF NOT EXISTS FOR (tr:Transcription)  ON (tr.created_at_ts)
CREATE INDEX IF NOT EXISTS FOR (i:Intent)          ON (i.source)
CREATE INDEX IF NOT EXISTS FOR (i:Intent)          ON (i.created_at_ts)
CREATE INDEX IF NOT EXISTS FOR (i:Intent)          ON (i.idempotency_key)
CREATE INDEX IF NOT EXISTS FOR (d:Dispatch)        ON (d.status)
CREATE INDEX IF NOT EXISTS FOR (d:Dispatch)        ON (d.paperclip_issue_id)
CREATE INDEX IF NOT EXISTS FOR (d:Dispatch)        ON (d.created_at_ts)
CREATE INDEX IF NOT EXISTS FOR (s:AgentSession)    ON (s.hermes_session_id)
CREATE INDEX IF NOT EXISTS FOR (s:AgentSession)    ON (s.paperclip_agent_id)
CREATE INDEX IF NOT EXISTS FOR (v:AgentDevice)     ON (v.hostname)
CREATE INDEX IF NOT EXISTS FOR (v:AgentDevice)     ON (v.last_seen_at_ts)
CREATE INDEX IF NOT EXISTS FOR (m:MemoryItem)      ON (m.kind)
CREATE INDEX IF NOT EXISTS FOR (m:MemoryItem)      ON (m.source)
CREATE INDEX IF NOT EXISTS FOR (m:MemoryItem)      ON (m.updated_at_ts)
CREATE INDEX IF NOT EXISTS FOR (p:ContextPacket)   ON (p.created_at_ts)
CREATE INDEX IF NOT EXISTS FOR (p:ContextPacket)   ON (p.query_hash)
```

---

## 3. Credentials and Environment Variables

### AssistX

```bash
# Neo4j (host enterprise container, not an infra service)
NEO4J_URI=bolt://host.docker.internal:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=knowledge_graph_2026
NEO4J_DATABASE=assistx

# API Security
BASIC_AUTH_USER=admin
BASIC_AUTH_PASS=change-me
API_TOKEN=<optional-token-for-/upload-audio>

# Redis
REDIS_URL=redis://redis:6379/0

# LLM Backend — "openai" (LM Studio / OpenAI API) or "ollama"
LLM_BACKEND=openai
OPENAI_BASE_URL=http://host.docker.internal:1234/v1
OPENAI_API_KEY=not-needed
LLM_MODEL=llama3.1:8b
EMBED_MODEL=nomic-embed-text

# Ollama (legacy, only if LLM_BACKEND=ollama)
OLLAMA_HOST=http://ollama:11434

# Optional Paths
TRANSCRIPTIONS_ROOT=./transcriptions
WHISPER_DEVICE=auto
WHISPER_COMPUTE_TYPE=int8
```

### Paperclip Integration ✅

```bash
# Required for Paperclip dispatch (local dev)
PAPERCLIP_API_URL=http://host.docker.internal:3100/api
PAPERCLIP_API_TOKEN=<token-from-paperclip-agent-keys>
PAPERCLIP_WORKSPACE_ID=<company-uuid-from-paperclip>

# Required for webhook signature verification
PAPERCLIP_WEBHOOK_SECRET=paperclip-dev-secret
```

### Hermes Agent (TODO)

```bash
# For hermes-paperclip-adapter
HERMES_CLI_PATH=/path/to/hermes
HERMES_SESSION_PERSIST_DIR=./hermes-sessions
HERMES_MEMORY_PROVIDER_URL=http://localhost:8000/api/brain/context

# Model/provider
HERMES_MODEL=gpt-4
HERMES_PROVIDER=openai
HERMES_API_KEY=<api-key>
```

---

## 4. Sample Payloads

### 4.1 Create Intent

```bash
curl -X POST http://localhost:8000/api/intents \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "source": "voice",
    "text": "Summarize recent conversations",
    "idempotency_key": "voice-2026-05-22-001",
    "client_ts": "2026-05-22T14:30:00Z",
    "metadata": {
      "user_id": "user-123",
      "device": "home-assistant",
      "confidence": 0.95
    }
  }'
```

**Response:**
```json
{
  "intent_id": "intent-abc123"
}
```

### 4.2 Create Context Packet

```bash
curl -X POST http://localhost:8000/api/brain/context \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "query": "Tasks related to customer support",
    "task_id": "task-xyz",
    "max_items": 20,
    "include_sources": ["memory", "knowledge", "orchestration"]
  }'
```

**Response:**
```json
{
  "context_packet": {
    "id": "packet-xyz123",
    "query": "Tasks related to customer support",
    "query_hash": "abc123...",
    "max_items": 20,
    "include_sources": ["memory", "knowledge", "orchestration"],
    "references": [
      {
        "type": "REFERENCES",
        "source": "orchestration",
        "source_type": "Task",
        "node": {
          "id": "task-1",
          "title": "Respond to support ticket",
          "status": "READY"
        }
      },
      {
        "type": "REFERENCES",
        "source": "memory",
        "source_type": "MemoryItem",
        "node": {
          "id": "memory-1",
          "kind": "note",
          "text": "Customer prefers email for support",
          "source": "voice"
        }
      }
    ],
    "created_at": "2026-05-22T14:35:00Z",
    "created_at_ts": 1747814100000
  }
}
```

### 4.3 Create Dispatch

```bash
curl -X POST http://localhost:8000/api/dispatch \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "task_id": "task-xyz",
    "target": {
      "paperclip_agent_id": "agent-local-01",
      "capabilities": ["terminal", "file", "code_execution", "web"]
    },
    "priority": "HIGH",
    "idempotency_key": "dispatch-task-xyz-001"
  }'
```

**Response:**
```json
{
  "dispatch_id": "dispatch-abc123"
}
```

### 4.3a Agent Polls and Claims Task Trigger

Tasks are the executable trigger primitive. `Intent` records classify incoming
input, but agents discover work by polling for `Task {status:'READY'}` nodes
that match their capabilities.

```bash
curl "http://localhost:8000/api/agent/tasks?capabilities=code_execution&agent_id=agent-local-01" \
  -u "neo4j:livelongandprosper"
```

```json
{
  "items": [
    {
      "id": "task-xyz",
      "title": "Analyze recent support tickets",
      "status": "READY",
      "required_capabilities": ["code_execution"]
    }
  ],
  "count": 1
}
```

```bash
curl -X POST http://localhost:8000/api/tasks/task-xyz/claim \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "agent_id": "agent-local-01",
    "capabilities": ["code_execution", "terminal"],
    "session_id": "session-hermes-001",
    "idempotency_key": "claim-task-xyz-agent-local-01"
  }'
```

```json
{
  "claimed": true,
  "task": {
    "id": "task-xyz",
    "status": "CLAIMED",
    "claimed_by": "agent-local-01"
  }
}
```

Agents should fetch fresh graph context after claiming, then heartbeat and
complete the task:

```bash
curl -X POST http://localhost:8000/api/brain/context \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "query": "Analyze recent support tickets",
    "task_id": "task-xyz",
    "session_id": "session-hermes-001",
    "include_sources": ["memory", "knowledge", "orchestration"]
  }'

curl -X POST http://localhost:8000/api/tasks/task-xyz/heartbeat \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{"agent_id":"agent-local-01","status":"RUNNING","metadata":{"progress":"started"}}'

curl -X POST http://localhost:8000/api/tasks/task-xyz/complete \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "agent_id": "agent-local-01",
    "status": "DONE",
    "summary": "Support ticket analysis completed.",
    "result": {"ok": true}
  }'
```

### 4.4 Write Memory Item

```bash
curl -X POST http://localhost:8000/api/memory/items \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "kind": "observation",
    "text": "Hermes successfully executed Python code to fetch customer data",
    "source": "hermes",
    "session_id": "session-hermes-001",
    "task_id": "task-xyz",
    "metadata": {
      "tool_used": "python_exec",
      "execution_time_ms": 234
    }
  }'
```

**Response:**
```json
{
  "memory_item_id": "memory-abc123"
}
```

### 4.5 Ingest Paperclip Event

```bash
curl -X POST http://localhost:8000/api/paperclip/events \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "event_type": "run_completed",
    "paperclip_issue_id": "issue-456",
    "paperclip_agent_id": "agent-local-01",
    "paperclip_run_id": "run-789",
    "event_id": "paperclip-event-2026-05-22-001",
    "payload": {
      "status": "completed",
      "duration_ms": 5000,
      "result_summary": "Task completed successfully",
      "artifacts": ["output.txt", "analysis.csv"]
    }
  }'
```

**Response:**
```json
{
  "paperclip_issue_id": "issue-456"
}
```

### 4.6 Update Agent Session

```bash
curl -X POST http://localhost:8000/api/sessions/session-hermes-001 \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "paperclip_agent_id": "agent-local-01",
    "hermes_session_id": "hermes-sess-xyz",
    "agent_identity": "hermes-worker-01",
    "device_id": "device-laptop-01",
    "platform": "linux",
    "metadata": {
      "model": "gpt-4",
      "provider": "openai",
      "region": "us-west-2"
    }
  }'
```

**Response:**
```json
{
  "session_id": "session-hermes-001"
}
```

---

## 5. Paperclip Integration Checklist

### Pre-Integration Tasks

- [ ] Identify Paperclip instance endpoint and API token
- [ ] Register AssistX workspace with Paperclip
- [ ] Obtain `PAPERCLIP_WORKSPACE_ID` and credentials
- [ ] Set up webhook endpoint for Paperclip → AssistX event delivery
- [ ] Test Paperclip API connectivity from AssistX container

### Expected Paperclip Workflow

1. **Issue Creation**: AssistX creates a Paperclip issue from a Task
   - Payload includes task title, description, context packet ID, capabilities required
   - Paperclip generates `paperclip_issue_id`

2. **Agent Assignment**: Paperclip assigns issue to a registered Hermes agent
   - Agent resolves by `paperclip_agent_id`
   - Agent spawns Hermes session with context and task instructions

3. **Execution**: Hermes agent executes via `hermes-paperclip-adapter`
   - Adapter stores Hermes session ID in AssistX AgentSession
   - Hermes queries context via `/api/brain/context` using memory provider
   - Hermes writes observations and results via `/api/memory/items`

4. **Completion**: Hermes reports result back to Paperclip
   - Paperclip stores result and artifacts
   - Paperclip sends `run_completed` event to AssistX `/api/paperclip/events`

5. **Reconciliation**: AssistX ingests result and updates graph
   - Task marked as DONE
   - Run artifacts linked
   - Memory updated with outcome

---

## 6. Implementation Epics

### Epic 1: Brain API and Neo4j Integration
- **Owner**: AssistX Core
- **Deliverable**: POST /api/intents, /api/brain/context, context packet creation and retrieval
- **Dependencies**: None
- **Timeline**: Week 1

### Epic 2: Hermes Memory Provider
- **Owner**: Hermes Integration
- **Deliverable**: External memory provider that calls AssistX Brain APIs
- **Dependencies**: Epic 1
- **Timeline**: Week 2

### Epic 3: Paperclip Dispatch
- **Owner**: Paperclip Integration
- **Deliverable**: Issue creation, agent assignment, event ingestion
- **Dependencies**: Epics 1, 2 + Paperclip endpoint available
- **Timeline**: Week 2-3

### Epic 4: Command Center UI
- **Owner**: AssistX UI/UX
- **Deliverable**: Intents, Dispatches, Sessions, Memory views + control endpoints
- **Dependencies**: Epics 1-3
- **Timeline**: Week 3-4

### Epic 5: Voice/TTS Integration
- **Owner**: Voice Team
- **Deliverable**: TTS events → Intents, cancellation/barge-in handling
- **Dependencies**: Epics 1-4
- **Timeline**: Week 4-5

---

## 7. Testing Strategy

### Unit Tests
- Neo4j schema creation and constraints
- Intent/ContextPacket/Dispatch/MemoryItem upserts
- Idempotency key deduplication
- Hermes provider API calls

### Integration Tests
- Full intent → context packet → dispatch → session → memory flow
- Paperclip event ingestion and state reconciliation
- Voice event deduplication and replay safety
- Cross-database or single-database namespace behavior

### End-to-End Tests
- Voice idea → Memory → Task → Dispatch → Hermes execution → result sync
- Cancellation and pause/resume flows
- Multi-device agent coordination

**Testing Infrastructure**: Docker + Neo4j ephemeral containers + pytest

---

## 8. Rollback Strategy

### Disable new features:
1. Stop the AssistX API
2. Remove Brain API endpoints from handlers
3. Revert to v1 task creation and execution paths
4. Keep Neo4j schema intact (read-only on new labels)

### Fallback behaviors:
- If Paperclip unavailable: enqueue tasks locally and use existing RQ workers
- If Hermes memory provider unavailable: Hermes uses built-in memory only
- If Neo4j unavailable: API returns 503; UI can display cached data

---

## 9. Success Criteria

✅ Phase 0 Complete When:
- [ ] All services documented and accessible
- [ ] Credentials and environment variables confirmed
- [ ] Sample payloads tested and verified
- [ ] Neo4j schema constraints and indexes active
- [ ] Paperclip endpoint and credentials available (or mocked)
- [ ] Implementation epics assigned and prioritized
