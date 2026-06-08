# AssistX — Docs Index

## Current Docs

| Doc | What it covers |
|-----|---------------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Paperclip cutover architecture, data flow, API reference, Neo4j schema |
| [`STATUS.md`](STATUS.md) | Verified cutover state, blocker, and remediation order |
| [`swarm_contracts/`](swarm_contracts/) | Deferred direct-worker contracts and event schema |
| [`plans/2026-06-08-xwing-agent-development-handoff.md`](plans/2026-06-08-xwing-agent-development-handoff.md) | Verified xwing-first worker readiness, endpoint inventory, and agent kickoff sequence |

## Archived Docs

Superseded or historical documents moved to [`archive/`](archive/):

| Doc | Why archived |
|-----|-------------|
| `UNIFICATION.md` | Concepts absorbed into ARCHITECTURE.md |
| `MIGRATION.md` | Original migration plan, superseded by swarm architecture |
| `HANDOFF_PHASE_2_SWARM_MVP.md` | Handoff doc, implementation complete |
| `HANDOFF_CURRENT_STATE.md` | Session handoff, done |
| `PHASE_2_HARDENING_PLAN.md` | Superseded by STATUS.md |
| `IMPLEMENTATION_GUIDE.md` | Superseded by ARCHITECTURE.md |
| `EXECUTION_SUMMARY.md` | Historical build summary |
| `PHASE_*.md` (0, 2, 3, 6-10) | Phase-specific plans, superseded |
| `OFFLINE_SWARM_INTEGRATION_PLAN.md` | Superseded by simpler Neo4j-centric vision |
| `SOPHIA_TO_ASSISTX_INTEGRATION_PLAN.md` | Historical draft; current release path is in ARCHITECTURE.md and STATUS.md |
| `CANARY_ACCEPTANCE_2026-05-24.md` | Historical acceptance record |
| `SPRINT_PLAN.md` | Archived, superseded |
| `WORK_ASSESSMENT.md` | Session assessment, done |
| `todo.md` | Items absorbed into STATUS.md |

## Key Source Files

| File | Purpose |
|------|---------|
| `src/assistx/api.py` | Main FastAPI app, all routes |
| `src/assistx/swarm_core.py` | Event envelope, task authority, policy helpers |
| `src/assistx/swarm_routes.py` | Swarm API endpoints |
| `src/assistx/neo4j_client.py` | Unified Neo4j driver |
| `src/assistx/paperclip_client.py` | Cutover execution client for Paperclip |
| `tests/test_swarm_phase2.py` | Swarm tests |
| `tests/test_migration_api.py` | Legacy migration tests |
| `deploy/swarm_nodes.example.json` | Seed node config |

## Run Tests

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_swarm_phase2.py tests/test_migration_api.py -v
```
