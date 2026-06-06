# AssistX - Paperclip Cutover Control Plane

AssistX owns task state and Sophia ingestion in Neo4j. For the current release,
non-realtime task execution is being stabilized through the existing Paperclip
service and its registered `hermes_local` adapter. Direct worker-claim/swarm
routing is development work and is not the cutover execution path.

```
Sophia (voice) -> POST /api/voice/events -> AssistX -> Paperclip issue
                                                    -> hermes_local run
                                                    -> synchronized outcome
```

## Quick Start

```bash
set -a; source .env; set +a
docker compose -f docker-compose.yml -f compose.override.yml up -d
docker exec -it assistx-api bash -lc "python -m assistx.cli init"
```

Optional overlay mode:

```bash
docker compose -f docker-compose.yml -f compose.overlay.yml up -d
```

## Key Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) - Release architecture, data flow, API, schema
- [`docs/STATUS.md`](docs/STATUS.md) - Verified status, blocker, and remediation order
- [`docs/swarm_contracts/`](docs/swarm_contracts/) - Future swarm/event contracts, gated from cutover
- `auto-router/docs/DEPLOYMENT.md` - Aligned deployment runbook for AssistX plus auto-router

## Core Concept

Task state and Sophia-linked graph records live in the `assistx` Neo4j
database. During Paperclip cutover, actionable Sophia input enters through
`POST /api/voice/events`, creates/link tasks in AssistX, and dispatches through
Paperclip. The direct worker claim endpoints are retained for development and
must not replace Paperclip until a separately approved release.

The main LLM path is LM Studio exposing OpenAI-compatible endpoints via
`OPENAI_BASE_URL`. The production Compose stack now requires that endpoint
to be set explicitly, so a missing or mispointed LM Studio configuration
fails fast instead of silently falling back. Ollama is not part of the
deployment stack.

AssistX can also run with an overlay topology where `auto-router` and
`auto-assign` sit alongside the core control plane. In that mode,
`ASSISTX_OVERLAY_MODE` and the `AUTO_ROUTER_*` / `AUTO_ASSIGN_*` URLs make
the overlay explicit instead of implicit.

## Run Tests

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_swarm_phase2.py tests/test_migration_api.py -v
```
