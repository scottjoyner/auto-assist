# System Vision Summary - Multi-Repo Coordination

**Last Updated**: June 3, 2026  
**Status**: Partially deployed, coordination layer needs completion

---

## Executive Summary

The system consists of **four interconnected repos** that work together to provide a multi-agent orchestration platform:

1. **auto-assist** - Task state authority and Sophia ingestion
2. **auto-router** - Model routing and quota management  
3. **auto-assign** - Assignment scheduling (not yet deployed)
4. **auto-ingest** - Background file processing pipeline

All repos sync to a shared Neo4j "brain" for context, with auto-assist using the new `assistx` database and auto-ingest writing to the legacy memory graph.

---

## Current Deployment Status

| Service | Port | Status | Notes |
|---------|------|--------|-------|
| **auto-router** | 8088 | ✅ Running (healthy) | Since May 29, 2026 - 4 days uptime |
| auto-router-redis | 6379 | ✅ Running (healthy) | Redis for quota tracking |
| **auto-assist-api** | 8000 | ⚠️ Responding | Containers not running but endpoints accessible |
| auto-assist-worker | 8000 | ❌ Not running | Background task processor |
| auto-assist-redis | 6379 | ❌ Not running | Task queue |
| auto-assist-ollama | 11434 | ❌ Not running | Intent classification LLM |
| **auto-assign** | 8090 | ❌ Not deployed | Scheduler service not yet started |

### Integration Endpoints Verified ✅

```
✅ http://localhost:8000/health                    - auto-assist API healthy
✅ http://localhost:8088/health                    - auto-router API healthy  
✅ http://localhost:8088/v1/models                 - Models available
✅ http://localhost:8088/admin/context             - Context projection working
```

---

## Repo Overview & Key Documentation

### 1. auto-assist (`~/git/auto-assist/`)

**Purpose**: Canonical task state, Sophia voice ingestion, Paperclip dispatch

**Key Files**:
- `README.md` - Quick start and overview
- `docs/ARCHITECTURE.md` - Release architecture, data flow, Neo4j schema
- `src/assistx/api.py` - Main FastAPI app (3300 lines)
- `neo4j_client.py` - Unified client for v1/v2 schemas

**Neo4j Database**: Uses `assistx` database (separate from legacy memory graph)  
**Password**: `livelongandprosper` (align with docker-compose)

**Task Lifecycle**:
```
READY → CLAIMED → RUNNING → DONE
                ├→ FAILED (retryable → READY)
                ├→ FAILED (terminal → FAILED)
                └→ CANCELLED
```

### 2. auto-router (`~/git/auto-router/`)

**Purpose**: OpenAI-compatible router, quota management, service discovery

**Key Files**:
- `README.md` - API surface and logical aliases
- `docs/HLD.md`, `docs/LLD.md` - High/low-level design
- `docs/NEO4J_ASSISTX_INTEGRATION.md` - Graph projection spec
- `config/providers.yaml` - Provider list (LM Studio, Cerebras, etc.)
- `config/policies.yaml` - Routing profiles

**Logical Model Aliases**:
- `auto/fast` - Normal interactive routing
- `auto/flash-start` - Cerebras WSE-3 planning
- `auto/high-quality` - Local draft + stronger refine
- `auto/code` - Code-focused path
- `auto/sophia` - Low-latency realtime
- `auto/backlog-burn` - Controlled quota burn
- `auto/local` - LM Studio only

### 3. auto-assign (`~/git/auto-assign/`)

**Purpose**: Assignment scheduling, heartbeat tracking, approval gating

**Key Files**:
- `README.md` - System role and responsibilities
- `docs/HLD.md`, `docs/LLD.md` - Architecture docs
- `docs/EVENT_CONTRACTS.md` - Event envelope specification
- `docs/INTEGRATION_CONTRACTS.md` - AssistX/router integration contracts

**Responsibilities**:
- Scheduler ticks and backlog evaluation
- Worker/node heartbeat ingestion
- Assignment scoring with explainable routing
- Task lease awareness and stale assignment visibility
- Approval gating before high-risk work
- Coordination between AssistX task state and router capabilities

### 4. auto-ingest (`~/git/auto-ingest/`)

**Purpose**: Background file processing pipeline for dashcam, phone logs, transcriptions

**Key Files**:
- `ingest_transcriptions.py` - Transcription ingestion script
- `dashcam_yolo_embeddings.py` - Dashcam video processing with YOLO embeddings

**Database**: Legacy Neo4j (`neo4j` or `memory`)  
**Password**: `livelongandprosper` (may differ from docker-compose)

---

## Integration Contracts

### auto-assist ↔ auto-router (Bidirectional Read-Only)

**auto-assist → auto-router**:
- `/api/router/context-projection` - Graph-backed context (nodes, providers, services)
- `/api/router/backlog-candidates` - Read-only task candidates for dry-run

**auto-router → auto-assist**:
- Event write-back via `/api/events` - Provenance events to Neo4j

### auto-assign ↔ auto-assist (Bidirectional Read/Write)

**auto-assign reads from AssistX**:
- Task candidates and policy context
- Canonical task state from Neo4j

**auto-assign writes to AssistX**:
- Assignment decisions as `assign.*` events
- Heartbeat summaries and lease transitions
- Trigger outcomes with provenance

### auto-assign ↔ auto-router (Read-Only)

**auto-assign queries router for**:
- Node/provider/quota snapshots
- Dry-run plans for assignment scoring
- Service discovery results

**Constraints**: Never bypass privacy/local-only rules

---

## Known Issues & Pitfalls

### 1. SQLite Schema Migration (auto-router)
**Problem**: Old DB schema causes startup crashes  
**Fix**: Swap out old DB, let new version create fresh one
```bash
cp data/router.sqlite3 data/router.sqlite3.preupgrade
rm data/router.sqlite3
docker compose up -d --force-recreate
```

### 2. Neo4j Password Mismatch
**Problem**: docker-compose uses `knowledge_graph_2026`, legacy apps use `livelongandprosper`  
**Fix**: Align passwords before running apps or restoring dumps

### 3. Docker Network Bridging
**Problem**: Auto-router and auto-assist on separate networks can't reach each other  
**Fix**: Connect networks explicitly
```bash
docker network connect auto-assist_default auto-router
# Add to docker-compose.yml for persistence
```

### 4. Root-Owned Data Directory
**Problem**: Docker volume writes create root-owned files in `data/`  
**Fix**: `sudo chown -R scott:scott data/` after deploy

### 5. Context Source Stays "bootstrap"
**Problem**: After fixing network, context still shows "bootstrap" instead of AssistX projection  
**Fix**: Force recreate container to reload context from newly available endpoint

---

## Documentation Sync Status

### Files Indexed in Neo4j

Using the `sync_docs_to_neo4j.py` script:

```bash
# Run full sync
python3 ~/git/auto-assist/scripts/sync_docs_to_neo4j.py --all

# Or just check status
python3 ~/git/auto-assist/scripts/sync_docs_to_neo4j.py --status
```

**Expected files to index**:
- auto-assist: ~15 .md files (README, docs/, src/prompts/)
- auto-router: ~20 .md files (README, docs/, .venv/docs)
- auto-assign: ~13 .md files (README, docs/)

### Key Documentation to Sync

**Critical Integration Docs**:
- `auto-assist/docs/ARCHITECTURE.md` - System architecture and data flow
- `auto-router/docs/HLD.md`, `LLD.md` - Router design
- `auto-router/docs/NEO4J_ASSISTX_INTEGRATION.md` - Graph integration spec
- `auto-assign/docs/EVENT_CONTRACTS.md` - Event envelope format
- `auto-assign/docs/INTEGRATION_CONTRACTS.md` - API contracts between repos

**Schema & Policy Docs**:
- `auto-assist/docs/swarm_contracts/` - Future swarm contracts (gated)
- `auto-router/docs/QUOTA_STRATEGY.md` - Quota burn-down model
- `auto-router/docs/SECURITY_PRIVACY.md` - Privacy constraints
- `auto-assign/docs/NEO4J_BRAIN_AND_CACHE_POLICY.md` - Neo4j as true brain

---

## Completion Validation Checklist

To confirm the system is fully coordinated and operational:

### ✅ Deployment Health
- [ ] auto-assist-api responding on port 8000
- [ ] auto-router healthy on port 8088  
- [ ] All Redis instances accessible
- [ ] Neo4j databases accessible (assistx + legacy)

### ✅ Integration Endpoints
- [ ] `/api/router/context-projection` returns nodes/providers/services
- [ ] `/api/router/backlog-candidates` returns task candidates
- [ ] Event write-back working bidirectionally
- [ ] No orphaned events in outboxes

### ✅ Documentation Synced to Neo4j
- [ ] All .md files indexed (auto-assist, auto-router, auto-assign)
- [ ] Key concepts searchable via Neo4j queries
- [ ] Cross-references between related docs created
- [ ] Last sync timestamps updated

### ✅ Coordination Layer Complete
- [ ] auto-assign deployed and running
- [ ] Scheduler ticks executing successfully
- [ ] Heartbeat ingestion working
- [ ] Assignment decisions being made with explainable reasons
- [ ] Approval gating functional for non-Scott speakers

---

## Next Steps for Full Vision Sync

### Immediate (This Week)
1. **Deploy auto-assign** if not already running
2. **Run documentation sync script** to index all .md files in Neo4j
3. **Verify integration contracts** are working end-to-end
4. **Document any gaps** in the coordination layer

### Short-Term (Next Sprint)
1. Set up monitoring for event flow between repos
2. Create automated health check dashboard
3. Add alerting for outbox backlog > 0
4. Document deployment runbook with common pitfalls

### Long-Term (Q3 2026)
1. Implement direct worker claiming (bypass Paperclip)
2. Add background scan/refresh cadence for service discovery
3. Model registry write-back to Neo4j
4. Sophia voice events → AssistX task lifecycle E2E

---

## Related Skills & Resources

- **coordination-intuition** - This skill provides the coordination layer overview
- **auto-router** - Deployment and operations details
- **assistx-command-center** - Neo4j-backed orchestration patterns
- **hermes-agent** - Multi-agent spawning and delegation
- **sophia-voice** - Voice sidecar architecture

---

## Quick Reference Commands

```bash
# Check all services
docker ps | grep -E "auto-|assistx"

# Verify health endpoints
curl http://localhost:8000/health | jq
curl http://localhost:8088/health | jq

# Sync docs to Neo4j
python3 ~/git/auto-assist/scripts/sync_docs_to_neo4j.py --all

# Check auto-router outbox
curl http://localhost:8088/admin/outbox | jq '.pending_count'

# Trigger scheduler tick (auto-assign)
curl -X POST http://localhost:8090/api/scheduler/tick

# Query Neo4j for documentation count
cypher> MATCH (d:Documentation) RETURN count(d), collect(distinct d.repo)
```

---

**Author**: scottjoyner  
**Skill Created**: June 3, 2026  
**Version**: 1.0.0
