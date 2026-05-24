# AssistX + Hermes + Neo4j + Paperclip Migration - Implementation Guide

## Quick Start

This guide provides step-by-step instructions for implementing the complete AssistX migration to become a multi-agent orchestration platform.

**Status**: Phases 0-4 infrastructure code completed, 15/15 tests passing
(10 migration API + 5 Hermes memory provider). Graph-first Task triggers
are implemented for agent polling, claiming, heartbeats, and completion.
Paperclip remains an optional assignment transport.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                      AssistX Command Center                          │
│                  (Dashboard, Task Management, Memory)                 │
└────────────────────┬────────────────────────────────────────────────┘
                     │
                ┌────▼─────────────────────────────────┐
                │   AssistX Brain APIs                 │
                │ ──────────────────────────────────── │
                │ • POST /api/intents                  │
                │ • POST /api/brain/context            │
                │ • GET  /api/agent/tasks              │
                │ • POST /api/tasks/{id}/claim         │
                │ • POST /api/dispatch                 │
                │ • POST /api/memory/items             │
                │ • POST /api/brain/signals            │
                └────┬──────────────────────┬──────────┘
                     │                      │
         ┌───────────▼──────────┐  ┌───────▼──────────────┐
         │                      │  │                      │
         │  Neo4j Graph Brain   │  │  Paperclip Hub       │
         │  ─────────────────   │  │  ──────────────────  │
         │ • Orchestration      │  │ • Issue creation     │
         │ • Memory (active)    │  │ • Agent assignment   │
         │ • Knowledge (ingest) │  │ • Event webhooks     │
         │                      │  │                      │
         └──────────────────────┘  └───────┬──────────────┘
                                           │
                    ┌──────────────────────┴───────────────┐
                    │                                      │
         ┌──────────▼────────────────┐      ┌─────────────▼──────┐
         │                           │      │                    │
         │  Hermes Local Agent       │      │ Hermes Remote      │
         │  (Memory Provider)        │      │ Agents (N devices) │
         │  ──────────────────       │      │ ──────────────────┘
         │ • Persistent sessions     │      │
         │ • Context retrieval       │      │
         │ • Tool execution          │      │
         │ • Memory writes           │      │
         │                           │      │
         └───────────────────────────┘      └────────────────────┘
```

---

## Implementation Phases

### Phase 0: Inventory and Contract Freeze ✅
**Deliverable**: [docs/PHASE_0_INVENTORY.md](docs/PHASE_0_INVENTORY.md)

Captures baseline infrastructure, services, credentials, and sample payloads.

**Key Items**:
- Service inventory (Neo4j, Redis, Paperclip, Hermes)
- Environment variables and configuration
- API endpoint reference
- Neo4j schema and constraints (already deployed)
- Sample payloads for all new APIs

**Status**: ✅ Complete - see PHASE_0_INVENTORY.md

---

### Phase 1: Brain Schema and Retrieval ✅
**Deliverable**: Neo4j orchestration schema + context packet retrieval

**Completed**:
- ✅ Constraints and indexes created via `neo.ensure_schema()`
- ✅ Intent creation with idempotency (`upsert_intent()`)
- ✅ Context packet creation with bounded retrieval (`create_context_packet()`)
- ✅ Source citation in references (orchestration, knowledge, memory)
- ✅ API endpoints: `/api/intents`, `/api/brain/context`, `/api/context-packets/{id}`
- ✅ Task trigger lifecycle: `/api/agent/tasks`, `/api/tasks/{id}/claim`, `/heartbeat`, `/complete`
- ✅ Ticket hierarchy on Task nodes: epics, stories, tasks, bugs, and chores
- ✅ `/api/ask` and `/api/ask_async` create Deliverable → Epic → Story → Task graphs

**Status**: ✅ Complete - graph brain and Task trigger lifecycle working

**Next**: Wire live Hermes adapters around polling, claiming, context prefetch, memory writeback, and completion

---

### Phase 2: Hermes Memory Integration 📋
**Deliverable**: [docs/PHASE_2_HERMES_MEMORY_INTEGRATION.md](docs/PHASE_2_HERMES_MEMORY_INTEGRATION.md)

Hermes external memory provider for graph-backed context and memory writes.

**Completed**:
- ✅ `HermesMemoryProvider` class implemented
- ✅ Methods: `prefetch()`, `write_memory()`, `signal_event()`, `update_session()`
- ✅ API endpoints: `/api/memory/items`, `/api/brain/signals`, `/api/sessions`
- ✅ Documentation and configuration guide

**Status**: ✅ Ready for integration testing

**Next**: 
- [ ] Write integration tests with mock Hermes
- [ ] Test actual Hermes session with memory provider
- [ ] Validate context prefetch and write flow

---

### Phase 3: Paperclip Dispatch Integration ✅
**Deliverable**: [docs/PHASE_3_PAPERCLIP_INTEGRATION.md](docs/PHASE_3_PAPERCLIP_INTEGRATION.md)

Optional task dispatch through Paperclip to Hermes agents. Neo4j `Task` nodes
remain the source of truth and the primary executable trigger.

**Completed**:
- ✅ Paperclip server running as systemd service at `http://127.0.0.1:3100`
- ✅ 3-line source patch for `local_trusted` + non-loopback bind (dev)
- ✅ `PaperclipClient` with routes matching real Paperclip API
- ✅ Company/agent/API key created in Paperclip
- ✅ Docker networking via `host.docker.internal:host-gateway`
- ✅ Optional Paperclip issue creation from `/api/dispatch`
- ✅ Local-only dispatch fallback when Paperclip is not configured
- ✅ Event ingestion endpoint: `/api/paperclip/events`
- ✅ **Live test verified**: dispatch → Paperclip issue created

**Clarification**:
- Paperclip is optional transport, not the source of truth.
- `/api/dispatch` creates local Neo4j dispatch records and adds `paperclip_issue_id` when Paperclip is configured.
- Paperclip has no outbound webhook API — event polling is the fallback.

**Live Test Result (May 23, 2026)**:
```
POST /api/tickets → {"ticket_id": "af1cffab..."}
POST /api/dispatch → {"dispatch_id": "b622a5f0...", "paperclip_issue_id": "2365b591...", "paperclip_error": null}
GET /api/issues/2365b591 → status=backlog, createdByAgentId=cfecc886-...
```

**Security Review**:
- HMAC webhook verification is optional (needs tightening for production)
- No rate limiting on dispatch/event endpoints
- Basic Auth uses plain string comparison
- See MIGRATION.md section 15 for hardening tasks

**Next**:
- [x] Test against a live Paperclip instance
- [ ] Add periodic poller to sync Paperclip issue status to Neo4j
- [ ] Verify live assignment to Hermes agents
- [ ] End-to-end: task → dispatch → issue → Hermes → result → sync

---

### Phase 4: Command Center UI ✅
**Deliverable**: Dashboard views and control endpoints

**Completed**:
- ✅ `/api/intents`, `/api/intents/{intent_id}` - view intents
- ✅ `/api/devices`, `/api/devices/{device_id}` - view agent devices
- ✅ `/api/memory`, `/api/memory/{memory_id}` - view memory items
- ✅ `/api/dispatches` - view dispatches (existing)
- ✅ `/api/sessions` - view agent sessions (existing)
- ✅ `/api/tasks/{task_id}/cancel`, `/pause`, `/resume` - task controls
- ✅ `/api/dispatches/{dispatch_id}/reassign` - dispatch controls

**Status**: ✅ API endpoints complete

**Next**:
- [ ] Expose READY/CLAIMED/RUNNING ticket state in UI
- [ ] Show epics/stories/tasks tree with agent/session/heartbeat context
- [ ] Wire up control buttons (cancel, pause, resume, reassign)
- [ ] Add filters and search

---

### Phase 5: Voice/TTS and Media Capture 📱
**Deliverable**: Browser/media capture intake, TTS events → Intents, cancellation handling

**Partially implemented**:
- ✅ `/ingest` page with audio/video browser recording
- ✅ `POST /api/captures` stores media + writes `MediaCapture`/`MediaAsset`/`Transcription`/`MemoryItem`/`Intent`/`SignalEvent` to Neo4j
- 🔲 Classify capture intents (memory-only, executable task, cancellation, etc.)
- 🔲 Connect TTS task events to `/api/intents` endpoint
- 🔲 Handle cancellation/barge-in events
- 🔲 Update active Task/Dispatch/AgentRun status

**Status**: 🔧 Media capture infrastructure done, classification and TTS wiring pending

---

### Phase 6: Hardening and Rollout 🔒
**Deliverable**: Production readiness

**Status**: 🔲 Not started

**Scope**:
- Authentication (API tokens, HMAC signatures)
- Operational dashboards (queue depth, latency, failures)
- Monitoring and alerting
- Canary deployment strategy
- Rollback procedures

---

## Installation and Setup

### 1. Environment Setup

```bash
# Install Python dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Create .env file
cat > .env << EOF
# Neo4j
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=livelongandprosper

# AssistX API
BASIC_AUTH_USER=neo4j
BASIC_AUTH_PASS=livelongandprosper

# Redis (for RQ queues)
REDIS_URL=redis://redis:6379/0

# Paperclip (Phase 3 — live, local dev)
PAPERCLIP_API_URL=http://host.docker.internal:3100/api
PAPERCLIP_API_TOKEN=<agent-api-key>
PAPERCLIP_WORKSPACE_ID=<company-uuid>
PAPERCLIP_WEBHOOK_SECRET=paperclip-dev-secret

# Hermes (when ready for Phase 2)
# HERMES_MEMORY_PROVIDER_ENABLED=true
EOF
```

### 2. Docker Compose

```bash
# Start services
docker-compose up -d neo4j redis

# Wait for Neo4j to be ready
docker-compose logs neo4j | grep "Started"
```

### 3. Run Tests

```bash
# Phase 1: Brain API tests
python -m pytest tests/test_migration_api.py::test_api_intent_and_context_packet -v

# Phase 3: Paperclip tests (when available)
python -m pytest tests/test_paperclip_integration.py -v
```

### 4. Start AssistX API

```bash
# Development mode (auto-reload)
uvicorn src.assistx.api:app --reload --host 0.0.0.0 --port 8000

# Production mode
gunicorn src.assistx.api:app --workers 4 --worker-class uvicorn.workers.UvicornWorker
```

---

## API Reference

### Intent Management

```bash
# Create intent (voice, webhook, dashboard, etc.)
curl -X POST http://localhost:8000/api/intents \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "source": "voice",
    "text": "Summarize recent tasks",
    "idempotency_key": "voice-123"
  }'

# List intents
curl http://localhost:8000/api/intents?source=voice -u "neo4j:livelongandprosper"

# Get intent details
curl http://localhost:8000/api/intents/{intent_id} -u "neo4j:livelongandprosper"
```

### Ask Deliverables

Every ask can create a user-facing deliverable. The deliverable is represented
as a `Task` ticket with `ticket_type=deliverable`, then broken into an Epic,
Story, and executable Task. Async answers publish a `deliverable_completed`
event on the answer stream when the deliverable is marked complete.

```bash
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "question": "Build the migration update",
    "mode": "async",
    "idempotency_key": "ask-migration-update-001"
  }'
```

Response includes `deliverable_id`, `epic_id`, `story_id`, and `task_id`.

### Context Retrieval

```bash
# Create context packet
curl -X POST http://localhost:8000/api/brain/context \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "query": "Customer support tasks",
    "max_items": 20,
    "include_sources": ["memory", "knowledge", "orchestration"]
  }'

# Get context packet
curl http://localhost:8000/api/context-packets/{packet_id} -u "neo4j:livelongandprosper"
```

### Dispatch and Execution

```bash
# Create local dispatch record (optional Paperclip transport comes later)
curl -X POST http://localhost:8000/api/dispatch \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "task_id": "task-xyz",
    "target": {"paperclip_agent_id": "agent-1", "capabilities": ["code"]},
    "priority": "HIGH"
  }'

# List dispatches
curl http://localhost:8000/api/dispatches -u "neo4j:livelongandprosper"

# Reassign dispatch
curl -X POST http://localhost:8000/api/dispatches/{dispatch_id}/reassign \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{"paperclip_agent_id": "agent-2"}'
```

### Task Trigger Lifecycle

```bash
# Agent polls READY tasks that match its capabilities
curl "http://localhost:8000/api/agent/tasks?capabilities=code_execution&agent_id=agent-1" \
  -u "neo4j:livelongandprosper"

# Agent atomically claims one task
curl -X POST http://localhost:8000/api/tasks/{task_id}/claim \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "agent_id": "agent-1",
    "capabilities": ["code_execution"],
    "session_id": "session-1",
    "idempotency_key": "claim-task-xyz-agent-1"
  }'

# Agent marks active work and stores heartbeat metadata
curl -X POST http://localhost:8000/api/tasks/{task_id}/heartbeat \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{"agent_id":"agent-1","status":"RUNNING","metadata":{"progress":"started"}}'

# Agent completes the task; AssistX writes AgentRun and optional outcome memory
curl -X POST http://localhost:8000/api/tasks/{task_id}/complete \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "agent_id": "agent-1",
    "status": "DONE",
    "summary": "Task completed.",
    "result": {"ok": true}
  }'
```

### Task Control

```bash
# Pause task
curl -X POST http://localhost:8000/api/tasks/{task_id}/pause -u "neo4j:livelongandprosper"

# Resume task
curl -X POST http://localhost:8000/api/tasks/{task_id}/resume -u "neo4j:livelongandprosper"

# Cancel task
curl -X POST http://localhost:8000/api/tasks/{task_id}/cancel -u "neo4j:livelongandprosper"
```

### Memory Management

```bash
# Write memory item
curl -X POST http://localhost:8000/api/memory/items \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "kind": "observation",
    "text": "Hermes noted that customer prefers evening calls",
    "source": "hermes"
  }'

# List memory
curl http://localhost:8000/api/memory?source=hermes -u "neo4j:livelongandprosper"

# Get memory item
curl http://localhost:8000/api/memory/{memory_id} -u "neo4j:livelongandprosper"
```

### Agent Sessions

```bash
# Update session
curl -X POST http://localhost:8000/api/sessions/session-1 \
  -H "Content-Type: application/json" \
  -u "neo4j:livelongandprosper" \
  -d '{
    "hermes_session_id": "hermes-xyz",
    "device_id": "laptop-1",
    "platform": "linux"
  }'

# List sessions
curl http://localhost:8000/api/sessions -u "neo4j:livelongandprosper"
```

---

## Testing

### Run All Tests

```bash
# All migration + Hermes tests (15 total)
python -m pytest tests/test_migration_api.py tests/test_hermes_memory_provider.py -v

# Single test
python -m pytest tests/test_migration_api.py::test_api_intent_and_context_packet -v
```

### Test Status (May 22, 2026)

**15/15 tests passing** — automated via ephemeral Neo4j 5.23 Docker container.

- `test_migration_api.py` — 10 tests covering intents, context, dispatch, sessions, memory, signals, task lifecycle, tickets, command center endpoints, and reassign.
- `test_hermes_memory_provider.py` — 5 tests covering prefetch, write, signal, session update, and token auth.

### Manual / Live Integration Tests

These require a running Paperclip instance or live Hermes agent:

#### Phase 1: Intent and Context

- ✅ Verified automatically in `test_api_intent_and_context_packet`

#### Phase 2: Hermes Memory Provider

- ✅ Provider unit tests pass (`test_hermes_memory_provider_*`)
- [ ] Configure real Hermes agent with AssistX URL
- [ ] Wire `HermesMemoryProvider` into agent config
- [ ] Verify end-to-end prefetch/write with live agent

#### Phase 3: Paperclip Dispatch

- ✅ Paperclip client mock tested in `test_ticket_hierarchy_and_paperclip_dispatch`
- [ ] Set up PAPERCLIP_* environment variables
- [ ] Register webhook URL with Paperclip
- [ ] Test live `create_issue()` → webhook event → status update

---

## Troubleshooting

### Neo4j Connection Issues

```bash
# Check Neo4j is running
docker-compose ps neo4j

# Check logs
docker-compose logs neo4j | tail -20

# Test connection
cypher-shell -u neo4j -p livelongandprosper "RETURN 1"
```

### AssistX API Errors

```bash
# Check health endpoint
curl http://localhost:8000/health

# Review logs
docker-compose logs assistx | tail -50

# Check Neo4j schema was created
curl -X GET http://localhost:8000/api/tasks -u "neo4j:livelongandprosper"
```

### Tests Failing

```bash
# Check Docker is running
docker ps

# Rebuild Neo4j container for tests
docker-compose down neo4j
docker volume prune
docker-compose up -d neo4j

# Run test with verbose output
python -m pytest tests/test_migration_api.py -vv -s
```

---

## Deployment Checklist

### Pre-Production

- [ ] All tests passing (unit + integration)
- [ ] Neo4j backups configured
- [ ] Redis persistence enabled
- [ ] Paperclip connectivity verified
- [ ] Hermes agents registered
- [ ] Monitoring and alerting in place
- [ ] Documentation reviewed

### Production Deployment

1. Deploy Neo4j schema constraints (if not already created)
2. Deploy AssistX code with new APIs
3. Set environment variables
4. Restart AssistX API
5. Register Paperclip webhook endpoint
6. Configure Hermes memory provider
7. Smoke test: create intent → context → dispatch → completion
8. Monitor logs and metrics

### Rollback

```bash
# If new API endpoints cause issues:
1. Revert code to previous version
2. Restart API
3. Keep Neo4j schema intact (read-only on new labels)

# If Paperclip integration fails:
1. Unset PAPERCLIP_* variables
2. Dispatch creation reverts to local-only
3. No data loss; can re-enable later
```

---

## Performance Optimization

### Neo4j Tuning

```cypher
# Check index usage
PROFILE MATCH (i:Intent {created_at_ts: {ts}}) RETURN i

# Monitor slow queries
CALL db.monitor.showStatus() YIELD output RETURN output
```

### Context Packet Optimization

- Limit `max_items` for large graphs
- Use query filtering to reduce result set
- Consider caching context for repeated queries
- Archive old memory items periodically

---

## Security Considerations

### Current

- ✅ Basic auth on all endpoints
- ✅ API token requirement for `/upload-audio`

### Recommended (Phase 6)

- [ ] JWT tokens for API access
- [ ] HMAC-SHA256 signature validation on webhooks
- [ ] Rate limiting on endpoints
- [ ] Input validation on all payloads
- [ ] Encryption of sensitive fields (e.g., API keys)
- [ ] Audit logging for sensitive operations

---

## Support and Debugging

### Useful Cypher Queries

```cypher
# Find all intents created today
MATCH (i:Intent)
WHERE i.created_at_ts > timestamp() - 86400000
RETURN i ORDER BY i.created_at_ts DESC LIMIT 10

# Find active dispatches
MATCH (d:Dispatch {status: "RUNNING"})
RETURN d, count(d) AS count

# Find memory items from Hermes
MATCH (m:MemoryItem {source: "hermes"})
RETURN m ORDER BY m.updated_at_ts DESC LIMIT 20

# List all agent sessions
MATCH (s:AgentSession)-[:ASSIGNED_TO]-(d:Dispatch)
RETURN s, d ORDER BY s.updated_at_ts DESC
```

### Monitoring Dashboard

Key metrics to track:
- Intent creation rate (per source)
- Context packet creation/retrieval latency
- Dispatch success rate
- Average task completion time
- Memory items written per session
- Neo4j query performance

---

## Next Steps

### Immediate (Week 1)

1. ✅ Complete Phase 0 inventory → DONE
2. ✅ Complete Phase 1 schema and retrieval → DONE
3. ✅ Complete Phase 2 Hermes provider docs → DONE
4. ✅ Complete Phase 3 Paperclip docs → DONE
5. ✅ Complete Phase 4 API endpoints → DONE

### Short-term (Weeks 2-3)

1. Run integration tests for all phases
2. Set up Paperclip connectivity
3. Test Hermes memory provider with actual agent
4. Build command-center UI views
5. Load test and performance tuning

### Medium-term (Weeks 4-5)

1. Voice/TTS integration (Phase 5)
2. Multi-device agent coordination
3. Advanced context ranking and relevance scoring
4. Memory item archival and cleanup

### Long-term (Week 6+)

1. Hardening and production readiness (Phase 6)
2. Canary deployment to one agent
3. Full rollout with monitoring
4. Optimization based on real-world usage

---

## Documentation Index

- [PHASE_0_INVENTORY.md](docs/PHASE_0_INVENTORY.md) - Baseline inventory and contracts
- [PHASE_2_HERMES_MEMORY_INTEGRATION.md](docs/PHASE_2_HERMES_MEMORY_INTEGRATION.md) - Hermes memory provider guide
- [PHASE_3_PAPERCLIP_INTEGRATION.md](docs/PHASE_3_PAPERCLIP_INTEGRATION.md) - Paperclip dispatch integration
- [MIGRATION.md](MIGRATION.md) - Original detailed migration plan

---

## Contributing

When adding new features:
1. Update relevant Phase documentation
2. Add tests (unit + integration)
3. Update this README if endpoints/config changes
4. Tag with Phase number in commit message (e.g., "Phase 3: Add agent capability matching")

---

## Questions?

See [MIGRATION.md](MIGRATION.md) Section 14 "Progress Summary for Handoff" for additional context.
