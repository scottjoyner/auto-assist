# Sprint Plan: Phase 2 Swarm MVP Hardening

## Goal
Harden and normalize the Phase 2 Offline Swarm MVP implementation so AssistX is production-ready as the task-state authority and swarm event intake layer.

## Current Status
- Phase 2 MVP complete and passing all tests (36 tests: 5 swarm + 31 migration)
- Known issues identified and documented in `docs/HANDOFF_PHASE_2_SWARM_MVP.md`
- Architectural contracts finalized in `docs/swarm_contracts/`

## What needs hardening

### 1. Swarm route bootstrap → normal include_router()
- **Current**: routes added via monkey-patch in `rate_limiter.py`
- **Fix**: add `app.include_router(swarm_router)` in `api.py` after app creation
- **Remove**: FastAPI constructor monkey-patch from `rate_limiter.py`
- **Files**: `src/assistx/api.py`, `src/assistx/rate_limiter.py`

### 2. Swarm schema into Neo4jClient.ensure_schema()
- **Current**: schema extended via bootstrap wrapper
- **Fix**: move constraints directly into `Neo4jClient.ensure_schema()` or call helper from there
- **Files**: `src/assistx/neo4j_client.py`, `src/assistx/swarm_core.py`

### 3. Auth wiring for swarm endpoints
- **Current**: swarm routes lack auth dependency (trusted-network-only)
- **Fix**: import and use the same `auth` dependency from legacy API
- **Validate**: unauthenticated requests return 401, Basic Auth succeeds
- **Files**: `src/assistx/swarm_routes.py`, `tests/test_swarm_phase2.py`

### 4. Add `lease_seconds` to task claim endpoint
- **Current**: leases default to 900 seconds
- **Fix**: accept `lease_seconds` in claim request body, preserve default
- **Validate**: custom lease durations are honored
- **Files**: `src/assistx/swarm_routes.py`, `src/assistx/swarm_core.py`, `tests/test_swarm_phase2.py`

### 5. Model endpoint probing service
- **Current**: event and graph records exist; probing is not implemented
- **Fix**: probe `/v1/models` for OpenAI-compatible endpoints
- **Emit**: `model.endpoint.discovered` events
- **Store**: `ModelEndpoint` and `Model` nodes with benchmarks
- **Files**: new `src/assistx/model_probe.py` or extend `swarm_core.py`

### 6. Local outbox client helper
- **Current**: server-side idempotency exists; client library is not
- **Fix**: add SQLite or JSONL outbox for Sophia/auto-ingest producers
- **Retry**: event delivery to `/api/events`, track delivered/conflict states
- **Files**: new `src/assistx/outbox_client.py`

### 7. Test coverage for new fixes
- Auth validation (unauthenticated 401, authenticated succeeds)
- Custom lease durations
- Model endpoint discovery
- Outbox client idempotency
- **Files**: `tests/test_swarm_phase2.py`

## Verification checklist

- [ ] `pytest tests/test_swarm_phase2.py tests/test_migration_api.py -v` passes (should be 36+)
- [ ] Swarm routes use normal `app.include_router()`
- [ ] Swarm schema is in `Neo4jClient.ensure_schema()`
- [ ] Unauthenticated request to `/api/events` returns 401
- [ ] Authenticated request to `/api/events` succeeds
- [ ] Task claim accepts and honors `lease_seconds`
- [ ] Model endpoint probing creates `ModelEndpoint` + `Model` nodes
- [ ] Outbox client dedupes and retries events
- [ ] No FastAPI monkey-patches remain
- [ ] Rate limiter no longer extends schema

## Deliverables

1. ✅ Swarm routes integrated with `app.include_router()`
2. ✅ Schema moved to `Neo4jClient.ensure_schema()`
3. ✅ All swarm endpoints use auth dependency
4. ✅ `lease_seconds` parameter in task claim
5. ✅ Model endpoint probing service
6. ✅ Local outbox client helper
7. ✅ Comprehensive test coverage
8. ✅ All tests passing
9. ✅ No FastAPI monkey-patches
10. ✅ Rate limiter simplified

## Implementation path

Follow the next-agent prompt in `docs/HANDOFF_PHASE_2_SWARM_MVP.md` exactly.

## Architecture reference

Read these in order:
1. `UNIFICATION.md`
2. `docs/swarm_contracts/database_unification.md`
3. `docs/swarm_contracts/task_authority.md`
4. `docs/swarm_contracts/event_envelope.md`
5. `docs/HANDOFF_PHASE_2_SWARM_MVP.md`

## Out of scope for this pass

- Sophia runtime audio/STT/TTS changes
- Hermes agent changes (Sophia/auto-ingest will integrate their own)
- UI dashboard updates (command center is already complete)
- Paperclip deprecation (optional delegation mirror remains)

---

**Status**: Ready for implementation. Phase 2 MVP is stable; hardening pass is next.
