# Coordination Layer Summary

**Created**: June 3, 2026  
**Author**: scottjoyner  
**Purpose**: Full vision sync across auto-assign, auto-router, and auto-assist repos

---

## What We Built

### 1. Coordination Skill (`coordination-intuition`)
**Location**: `~/.hermes/skills/multi-agent/coordination-intuition/SKILL.md`

Provides the "brain" for coordinating work across all repos:
- System overview with architecture diagram
- Detailed repo descriptions and key files
- Integration contracts between services
- Deployment checklist with commands
- Known issues and pitfalls section
- Completion validation criteria

### 2. Documentation Sync Script (`sync_docs_to_neo4j.py`)
**Location**: `~/git/auto-assist/scripts/sync_docs_to_neo4j.py`

Python script that:
- Finds all .md files in three repos (auto-assign, auto-router, auto-assist)
- Extracts metadata (title, sections, code blocks, word count)
- Creates Neo4j nodes with full content and properties
- Indexes key concepts using APOC labels
- Creates relationships between related docs

**Usage**:
```bash
python3 ~/git/auto-assist/scripts/sync_docs_to_neo4j.py --sync    # Sync only
python3 ~/git/auto-assist/scripts/sync_docs_to_neo4j.py --status  # Check deployment
python3 ~/git/auto-assist/scripts/sync_docs_to_neo4j.py --verify  # Verify endpoints
python3 ~/git/auto-assist/scripts/sync_docs_to_neo4j.py --all     # Run all checks
```

### 3. Coordination Check Script (`coordination_check.py`)
**Location**: `~/git/auto-assist/scripts/coordination_check.py`

Comprehensive health check and reporting tool:
- Checks service health (auto-assist, auto-router)
- Validates integration endpoints
- Queries Neo4j for documentation count
- Generates formatted reports with issues

**Usage**:
```bash
python3 ~/git/auto-assist/scripts/coordination_check.py --all    # Full check
python3 ~/git/auto-assist/scripts/coordination_check.py --health # Health only
python3 ~/git/auto-assist/scripts/coordination_check.py --report # Report only
```

### 4. System Vision Summary (`SYSTEM_VISION_SUMMARY.md`)
**Location**: `~/git/auto-assist/docs/SYSTEM_VISION_SUMMARY.md`

Comprehensive documentation covering:
- Executive summary of the system
- Current deployment status (✅ auto-router running, ❌ auto-assign not deployed)
- Repo overviews with key files and purposes
- Integration contracts between services
- Known issues and pitfalls with fixes
- Documentation sync status
- Completion validation checklist
- Next steps for full vision sync

### 5. Delegation Skill (`repo-coordination-delegation`)
**Location**: `~/.hermes/skills/multi-agent/repo-coordination-delegation/SKILL.md`

Enables delegation to local models (qwen3.5-4b-claude) with:
- Four task templates for common coordination work
- Context packets for system overview, integration contracts, documentation inventory
- Validation steps after delegation completes
- Common pitfalls section
- Example delegation prompt

---

## Current System State

### ✅ Running Services
| Service | Port | Status | Since |
|---------|------|--------|-------|
| auto-router | 8088 | Healthy | May 29, 2026 (4 days) |
| auto-router-redis | 6379 | Healthy | May 29, 2026 |

### ⚠️ Responding but Containers May Be Down
| Service | Port | Status | Notes |
|---------|------|--------|-------|
| auto-assist-api | 8000 | Endpoints responding | Docker containers not running per `docker ps` |

### ❌ Not Deployed/Running
| Service | Port | Expected Role |
|---------|------|---------------|
| auto-assign | 8090 (planned) | Assignment scheduling, heartbeat tracking |
| auto-assist-worker | 8000 | Background task processing |
| auto-assist-redis | 6379 | Task queue |
| auto-assist-ollama | 11434 | Intent classification LLM |

### Integration Endpoints Verified ✅
```
✅ http://localhost:8000/health                    - auto-assist API healthy
✅ http://localhost:8088/health                    - auto-router API healthy  
✅ http://localhost:8088/v1/models                 - Models available
✅ http://localhost:8088/admin/context             - Context projection working
```

---

## How to Use These Tools

### Quick Health Check
```bash
# Run full system check
python3 ~/git/auto-assist/scripts/coordination_check.py --all
```

### Sync All Documentation to Neo4j
```bash
# Index all .md files from three repos
python3 ~/git/auto-assist/scripts/sync_docs_to_neo4j.py --sync
```

### Generate Coordination Report
```bash
# Get formatted report with health, integration status, and issues
python3 ~/git/auto-assist/scripts/coordination_check.py --report
```

### Delegate to Local Model (qwen3.5-4b-claude)
Use the `repo-coordination-delegation` skill in Hermes Agent:
```bash
hermes -s repo-coordination-delegation \
  -q "Perform full system audit and documentation sync across all repos"
```

---

## Next Steps for Full Vision Sync

### Immediate (This Week)
1. **Deploy auto-assign** if not already running
   ```bash
   cd ~/git/auto-assign
   docker compose up --build -d  # or whatever deployment method is used
   ```

2. **Run documentation sync** to index all .md files in Neo4j
   ```bash
   python3 ~/git/auto-assist/scripts/sync_docs_to_neo4j.py --sync
   ```

3. **Verify integration contracts** are working end-to-end
   - Test `/api/router/context-projection` returns nodes/providers/services
   - Test `/api/router/backlog-candidates` returns task candidates
   - Verify event write-back to Neo4j is bidirectional

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

## Key Files Reference

| File | Purpose | Location |
|------|---------|----------|
| `SKILL.md` (coordination-intuition) | System overview and coordination patterns | `~/.hermes/skills/multi-agent/coordination-intuition/SKILL.md` |
| `sync_docs_to_neo4j.py` | Documentation sync script | `~/git/auto-assist/scripts/sync_docs_to_neo4j.py` |
| `coordination_check.py` | Health check and reporting | `~/git/auto-assist/scripts/coordination_check.py` |
| `SYSTEM_VISION_SUMMARY.md` | Comprehensive system documentation | `~/git/auto-assist/docs/SYSTEM_VISION_SUMMARY.md` |
| `SKILL.md` (repo-coordination-delegation) | Delegation patterns for local models | `~/.hermes/skills/multi-agent/repo-coordination-delegation/SKILL.md` |

---

## Validation Queries

### Neo4j Documentation Count
```cypher
MATCH (d:Documentation) 
RETURN count(d) as total_docs, 
       collect(distinct d.repo) as repos,
       collect(d.last_synced) as last_sync
```

### Integration Endpoint Status
```bash
# Check all endpoints in one command
curl -s http://localhost:8000/health | jq '.status' && \
curl -s http://localhost:8088/health | jq '.status' && \
echo "All endpoints healthy" || echo "Some endpoints down"
```

### Outbox Status (auto-router)
```bash
# Check for orphaned events
curl -s http://localhost:8088/admin/outbox | jq '{pending_count, dispatched_count}'
```

---

## Related Skills & Resources

- **hermes-agent** - General delegation and subagent patterns
- **auto-router** - Router deployment and operations details
- **assistx-command-center** - Neo4j-backed orchestration patterns  
- **sophia-voice** - Voice sidecar architecture
- **auto-ingest** - Background file processing pipeline

---

## Quick Commands Cheat Sheet

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

# Query Neo4j for documentation
cypher> MATCH (d:Documentation) RETURN count(d), collect(distinct d.repo)

# Trigger scheduler tick (auto-assign, when deployed)
curl -X POST http://localhost:8090/api/scheduler/tick

# Generate coordination report
python3 ~/git/auto-assist/scripts/coordination_check.py --report
```

---

**Status**: Coordination layer built and documented. Ready for deployment validation and documentation sync.  
**Next Action**: Run `sync_docs_to_neo4j.py --all` to index all .md files in Neo4j.
