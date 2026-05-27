# AssistX — Offline Swarm Task Authority

AssistX is the **task-state authority** for an offline swarm of AI agents. It receives events from voice/auth edge (Sophia) and ingestion tools (auto-ingest), owns the authoritative task lifecycle in Neo4j, and makes work available for workers to claim and execute directly.

```
Sophia (voice) → POST /api/events → AssistX → Neo4j (tasks)
                                              ↑
Worker (Hermes/opencode) → claim → work → complete → publish events
```

## Quick Start

```bash
set -a; source .env; set +a
docker compose -f docker-compose.yml -f compose.override.yml up -d
docker exec -it assistx-api bash -lc "python -m assistx.cli init"
```

## Key Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — Current architecture, data flow, API, schema
- [`docs/STATUS.md`](docs/STATUS.md) — Implementation status and next steps
- [`docs/swarm_contracts/`](docs/swarm_contracts/) — Event envelope, task authority, node registry contracts

## Core Concept

All external input arrives via a unified event envelope (`POST /api/events`). Workers claim tasks directly from AssistX via REST (not through a separate orchestrator). Task state lives in the `assistx` Neo4j database. Workers report results back through the event API.

## Run Tests

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_swarm_phase2.py tests/test_migration_api.py -v
```
