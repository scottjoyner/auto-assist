# Current State Handoff — May 26, 2026

## What has changed since last session

The project has pivoted to implement an **Offline Swarm Compute architecture** as Phase 2 of the AssistX evolution. This is a significant shift from the Sophia enrollment + Paperclip cutover plan that was in flight.

### Key architectural decisions made

1. **AssistX is the task-state authority** for the entire swarm
2. **Database unification strategy**:
   - `neo4j` (main) = unified Scott historical memory graph
   - `assistx` = orchestration/control-plane state
   - `memory` (Sophia) = transitional staging until migration
3. **Network fabric**: Tailscale-first, offline-capable
4. **No more long-running task authorities**: Sophia, auto-ingest are event producers only

### Phase 2 MVP implementation status

✅ **Implemented**:
- Event envelope validation with replay idempotency
- Swarm node registration and heartbeat
- Task authority endpoints (fail, lease release)
- Voice policy stub for quick approval
- 8 MVP event types
- Graph schema for swarm nodes, capabilities, model endpoints
- Tests for swarm behavior

⚠️ **Known issues requiring hardening**:
1. Swarm routes use a bootstrap monkey-patch instead of normal `app.include_router()`
2. Swarm endpoints lack the same auth dependency as legacy API
3. Schema extension is patched into `rate_limiter.py` instead of `Neo4jClient.ensure_schema()`
4. Task claim endpoint does not accept `lease_seconds` parameter
5. Model endpoint probing is not implemented
6. Local outbox client for producers (Sophia, auto-ingest) is not implemented

## Files of interest

### Architecture & contracts
- `UNIFICATION.md` — shared cross-repo unification plan (READ FIRST)
- `docs/HANDOFF_PHASE_2_SWARM_MVP.md` — detailed MVP handoff
- `docs/swarm_contracts/*.md` — 8 concrete contracts

### Implementation
- `src/assistx/swarm_core.py` — event envelope, task authority, policy helpers
- `src/assistx/swarm_routes.py` — API endpoints (needs auth wiring)
- `tests/test_swarm_phase2.py` — swarm MVP tests (all passing)
- `deploy/swarm_nodes.example.json` — seed config

### Previous work (archived context)
- `SPRINT_PLAN.md` — old Sophia/Paperclip cutover plan (superseded)
- `docs/PHASE_11_FULL_ARCHITECTURE_PLAN.md` — old Phase 11 docs (archived)

## Next steps (for the next agent)

The handoff document in `docs/HANDOFF_PHASE_2_SWARM_MVP.md` contains a detailed next-agent prompt. TL;DR:

1. Replace swarm route bootstrap with normal `app.include_router()` in `api.py`
2. Move swarm schema into `Neo4jClient.ensure_schema()` directly
3. Wire all swarm endpoints to use the same auth as legacy API
4. Add `lease_seconds` parameter to task claim endpoint
5. Implement model endpoint probing service
6. Implement local outbox client helper
7. Add auth and lease-duration tests
8. Verify: `pytest tests/test_swarm_phase2.py tests/test_migration_api.py -v`

## Run tests to verify current state

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_swarm_phase2.py tests/test_migration_api.py -v
```

All tests should pass (currently 31 passed in migration API, swarm tests added).

## Git state

The repo is up-to-date with main. Recent commits:
- `edaa729a` — Index offline swarm Phase 2 handoff docs
- `05a068a5` — Document offline swarm Phase 2 MVP handoff
- Multiple commits adding swarm routes, schema, contracts, tests

---

**Status**: Phase 2 MVP complete and passing tests. Ready for hardening pass.
