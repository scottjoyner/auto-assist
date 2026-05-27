# Sprint Plan: ARCHIVED - Superseded by Phase 2 Swarm MVP Hardening

## Previous Goal (ARCHIVED)
Finish a safe cutover path for the existing live Paperclip + Hermes adapter deployment by closing the security, graph contract, Hermes provider, and UI integration gaps, then validating with an end-to-end canary.

**Status**: This plan has been superseded by the Phase 2 Offline Swarm MVP, which represents a major architectural shift from Paperclip-centric task routing to swarm event-driven orchestration.

## Sprint duration
2 weeks (starting immediately)

## Scope
- Secure Sophia operator enrollment and voice ingestion
- Complete graph contract for Sophia captures, tasks, and meetings
- Repair Hermes provider/runtime regressions and isolate fleet changes
- Align AssistX UI with same-origin integration and `/api/ops/status`
- Validate the Paperclip cutover path before retiring the legacy Hermes poller

## Priority backlog

### 1. Sophia enrollment & ingestion security
- Secure `/voiceprints/enroll` so privileged enrollment requires auth or one-time proof
- Remove or protect any open unauthenticated admin enrollment route
- Wire `SOPHIA_ADMIN_TOKEN` through `docker-compose.yml` and `.env` only
- Ensure AssistX forwards admin override to Sophia server-side
- Add route tests for valid/invalid enrollment requests
- Make `/api/voice/events` the canonical Sophia ingestion endpoint and keep `/api/sophia/events` as compatibility only

Files:
- `src/assistx/api.py`
- `docker-compose.yml`
- `.env.example`
- `tests/test_migration_api.py`

### 2. Graph semantics and capture/task contract
- Define canonical Sophia capture model: `MediaCapture` + `origin="sophia_voice"`
- Upsert capture nodes and link them to generated `Intent`, `MemoryItem`, `Task`, and `SignalEvent`
- Ensure meeting events link `Meeting` -> `Task` when task creation is actionable
- Add regression coverage for capture-to-task and meeting linkage

Files:
- `src/assistx/neo4j_client.py`
- `src/assistx/api.py`
- `tests/test_migration_api.py`

### 3. Hermes provider / fleet stabilization
- Restore the provider fallback contract so `_resolve_auto()` returns `(None, None)` cleanly
- Move fleet config to the supported runtime config scope
- Load runtime configuration values instead of hardcoded defaults
- Remove or globally handle the mutable fleet cache until fleet routing is proven
- Add/fix Hermes provider regression tests for isolated state and fallback path

Files:
- `src/assistx/agents/hermes_memory_provider.py`
- `src/assistx/auxiliary_client.py` (if present)
- `tests/test_hermes_memory_provider.py`
- `tests/test_auxiliary_client.py`

### 4. Paperclip cutover & deployment alignment
- Update docs to reflect the live local Paperclip service and registered `hermes_local` adapter
- Verify `paperclip_poller.py` syncs issue/run state correctly and preserves run lineage
- Keep the direct `hermes-agent-adapter` poller enabled until the Paperclip canary is validated
- Add deterministic Paperclip dispatch IDs and race-resistant dispatch linking

Files:
- `src/assistx/paperclip_poller.py`
- `src/assistx/neo4j_client.py`
- `docs/PHASE_11_FULL_ARCHITECTURE_PLAN.md`

### 5. AssistX UI and same-origin integration
- Remove browser-side direct localhost calls to Sophia and Paperclip services
- Proxy or expose same-origin AssistX endpoints for Sophia status/enrollment and Paperclip operational state
- Align dashboard JavaScript with `/api/ops/status`
- Validate the operator UI in the browser after changes

Files:
- `templates/voice.html`
- `templates/ops_dashboard.html`
- `static/` JS assets as needed

### 6. Canary and acceptance gating
- Execute a full Sophia-originated canary through AssistX → Paperclip → Hermes → completion
- Confirm the chain includes:
  - signed Sophia ingest
  - canonical `MediaCapture`
  - linked `Intent`/`Task`
  - Paperclip issue create
  - Hermes `hermes_local` assignment
  - synchronized completion in AssistX
- If the canary fails, preserve the legacy direct poller as the rollback path

Files:
- `src/assistx/api.py`
- `src/assistx/paperclip_poller.py`
- `src/assistx/agents/hermes_agent_adapter.py`

## Deliverables
- `SPRINT_PLAN.md` created in repo root
- Secure Sophia enrollment and admin override path implemented
- Graph capture/task linkage fully validated
- Hermes provider regression fixed and fleet changes isolated
- UI integration aligned with AssistX same-origin endpoints
- Paperclip + Hermes canary completed and release gate documented
- Release status in docs updated from `implemented` to `in progress` until runtime validation passes

## Acceptance criteria
- `pytest -q tests/test_migration_api.py` passes locally
- relevant Hermes tests pass
- Sophia admin enrollment is rejected without auth and accepted with valid override
- a Paperclip round-trip canary succeeds without falling back to the broken legacy process adapter
- `docs/PHASE_11_FULL_ARCHITECTURE_PLAN.md` and release notes accurately reflect the current live state

## Risks
- Paperclip still lacks outbound webhook support, so poller sync is required
- local-trusted Paperclip overrides are fragile and may break on upgrades
- UI direct-service integration is a soft failure mode if localhost calls remain
- fleet routing must remain disabled for this cutover unless fully validated

## Ready-to-start checklist
- [x] `SPRINT_PLAN.md` exists
- [ ] Sophia admin secret configured in runtime env only
- [ ] current Paperclip local service verified as active
- [ ] `hermes-agent-adapter.service` enabled and running
- [ ] direct poller retention plan documented
- [ ] canary validation script/steps defined

---

## Next step
Begin work on the first sprint epic: Sophia enrollment hardening and capture contract validation.
