# Handoff: Offline Swarm Phase 2 MVP

_Last updated: 2026-05-26_

## Purpose

This handoff summarizes the current state of the AssistX Phase 2 MVP for the offline swarm compute architecture.

The goal of this pass was to make AssistX ready to receive swarm events, own task authority, register nodes/capabilities, reconcile events into Neo4j, and provide the foundation for Sophia and auto-ingest to integrate next.

This document is for the next engineer or agent picking up the work.

---

## Source documents to read first

Read these in order:

1. `UNIFICATION.md`
2. `docs/swarm_contracts/database_unification.md`
3. `docs/swarm_contracts/task_authority.md`
4. `docs/swarm_contracts/event_envelope.md`
5. `docs/swarm_contracts/node_registry.md`
6. `docs/swarm_contracts/model_endpoint_registry.md`
7. `docs/swarm_contracts/voice_auth_policy.md`
8. `docs/swarm_contracts/auto_ingest_memory_enrichment.md`
9. this handoff document

---

## Current architecture decision

AssistX is the source of truth for task state and orchestration.

The current intended split is:

```text
AssistX / assistx DB      = tasks, leases, policy, dispatch, node registry, event intake
Main neo4j DB             = durable Scott historical memory graph
Sophia memory DB          = transitional voice/auth staging until migrated or synced
auto-ingest               = periodic historical memory enrichment, not the real-time control path
Tailscale/LAN             = default offline network fabric
Paperclip                 = optional delegation/human coordination mirror, not task authority
```

---

## Files added or changed in the Phase 2 MVP pass

### New runtime modules

```text
src/assistx/swarm_core.py
src/assistx/swarm_routes.py
```

### Modified runtime module

```text
src/assistx/rate_limiter.py
```

`rate_limiter.py` now bootstraps the swarm router and extends `Neo4jClient.ensure_schema()` because `api.py` imports `rate_limiter` before creating the FastAPI app. This avoided replacing the large legacy `api.py` file.

### New test file

```text
tests/test_swarm_phase2.py
```

### New seed/config example

```text
deploy/swarm_nodes.example.json
```

### Shared contracts already present

```text
docs/swarm_contracts/*.md
```

---

## New API endpoints

The swarm router adds:

```text
POST /api/events
POST /api/swarm/nodes/register
POST /api/swarm/nodes/{node_id}/heartbeat
GET  /api/swarm/nodes
GET  /api/swarm/capabilities
POST /api/tasks/{task_id}/fail
POST /api/tasks/leases/release-expired
GET  /api/policy/voice-action
```

Important security note: the new swarm router was added through a bootstrap shim and currently does not reuse the legacy `api.py` Basic Auth dependency. Treat these endpoints as trusted-network/Tailscale-only until the next pass wires them to the same auth policy as the rest of AssistX.

---

## Implemented graph support

The MVP adds schema helpers for:

```text
SwarmNode
ServiceEndpoint
Capability
ModelEndpoint
Model
HealthCheck
ArtifactRef
EventEnvelope
VoiceAuthDecision
IngestBatch
MemoryCandidate
PolicyDecision
Task.lease_expires_at_ts index
```

Nested dict/list payloads are serialized into `*_json` sidecar properties before writing to Neo4j. This avoids Neo4j property-type errors while preserving source event detail.

---

## Event envelope behavior

`record_event()` implements:

- schema version validation
- required field validation
- privacy and retention field validation
- deterministic payload hashing
- event replay dedupe by `event_id` or `idempotency_key`
- conflict detection when the same idempotency key arrives with changed payload
- graph reconciliation for MVP event types

Implemented MVP event types:

```text
voice.quick_input.created
voice.auth.decision
ingest.batch.started
ingest.memory_candidate.created
ingest.batch.review_ready
swarm.node.registered
swarm.node.heartbeat
model.endpoint.discovered
```

---

## Task authority behavior added

The MVP adds:

- `fail_task()` helper
- `POST /api/tasks/{task_id}/fail`
- retryable failures return tasks to `READY`
- non-retryable failures set tasks to `FAILED`
- task leases are added on claim and heartbeat through the bootstrap wrapper
- expired leases can be released with `POST /api/tasks/leases/release-expired`

Known limitation: the existing claim endpoint does not yet accept a `lease_seconds` field. The wrapper defaults to 900 seconds.

---

## Voice policy behavior added

The MVP policy stub implements:

- `authenticated_scott` low-risk auto-approval
- `admin_override` low-risk auto-approval
- `unknown_speaker` requires approval
- high-risk action always requires approval

This is not the final voice auth implementation. Sophia runtime audio, STT, TTS, and voiceprint changes are intentionally out of scope for this pass.

---

## Tests added

Run:

```bash
python -m pytest tests/test_swarm_phase2.py -v
python -m pytest tests/test_migration_api.py -v
```

The tests cover:

- event replay idempotency
- conflicting payload hash detection
- unknown speaker requiring approval
- Scott-authenticated low-risk action auto-approval
- swarm node registration
- capability listing
- task claim / heartbeat / completion / failure
- lease release back to `READY`
- route availability for the new swarm endpoints

The tests rely on the existing Dockerized Neo4j fixture.

---

## Known risks and follow-up fixes

### 1. Swarm routes need legacy auth dependency

Current status: endpoints are functionally present but should be treated as trusted-network-only.

Next agent should wire the swarm router to the same auth behavior used by `api.py`.

Acceptance check:

```text
Unauthenticated request to /api/events returns 401.
Authenticated Basic Auth request succeeds.
Trusted header behavior remains consistent with the legacy API.
```

### 2. Replace FastAPI monkey-patch with normal router include

Current status: the router is attached through `rate_limiter.py` because editing `api.py` directly was intentionally avoided.

Preferred long-term fix:

```python
from .swarm_routes import router as swarm_router
app.include_router(swarm_router)
```

Add this directly in `api.py` after `app = FastAPI(...)`, then remove the FastAPI constructor patch from `rate_limiter.py`.

### 3. Move schema extension directly into `Neo4jClient.ensure_schema()`

Current status: `ensure_schema()` is extended through a bootstrap wrapper.

Preferred long-term fix:

- import `ensure_swarm_schema` directly or inline the constraints in `neo4j_client.py`
- remove the monkey-patched schema wrapper from `swarm_routes.py`

### 4. Add `lease_seconds` to the claim API body

Current status: leases default to 900 seconds.

Next change:

- add `lease_seconds: int = 900` to the task claim request model
- pass it into the lease setter after successful claim
- add test coverage for custom lease durations

### 5. Add model endpoint service probing

Current status: event and model endpoint graph records exist; active probing is not implemented.

Next change:

- probe `/v1/models` for OpenAI-compatible/LM Studio endpoints
- write `model.endpoint.discovered` events
- store benchmarks in `BenchmarkResult`

### 6. Add local outbox client

Current status: server-side idempotency exists; client outbox replay library is not implemented.

Next change:

- add local SQLite or JSONL outbox helper for Sophia and auto-ingest
- retry event delivery to `/api/events`
- mark delivered/conflict states

---

## Next agent implementation prompt

Use this prompt for the next pass:

```text
You are working in scottjoyner/auto-assist.

Read UNIFICATION.md, docs/HANDOFF_PHASE_2_SWARM_MVP.md, and docs/swarm_contracts/*.md.

Goal: harden and normalize the Phase 2 Offline Swarm MVP implementation.

Tasks:
1. Replace the swarm route bootstrap monkey-patch with a normal `app.include_router()` in `src/assistx/api.py`.
2. Move the swarm schema extension into `Neo4jClient.ensure_schema()` or a direct helper call from that method.
3. Make all `/api/events` and `/api/swarm/*` endpoints use the same Basic Auth / trusted-header behavior as the legacy API.
4. Add `lease_seconds` to the task claim request body and preserve default 900-second leases.
5. Add a model endpoint probe service that discovers `/v1/models` from registered OpenAI-compatible endpoints and emits `model.endpoint.discovered` events.
6. Add a local outbox client helper for Sophia and auto-ingest producers.
7. Add tests proving unauthenticated swarm endpoints return 401, authenticated calls succeed, custom lease durations are honored, and model endpoint discovery records `ModelEndpoint` + `Model` nodes.
8. Run `python -m pytest tests/test_swarm_phase2.py tests/test_migration_api.py -v`.

Do not implement runtime Sophia audio/STT/TTS changes yet. This pass should harden AssistX as the swarm authority and event intake layer.
```

---

## Handoff summary

AssistX now has the first MVP pieces required to act as the offline swarm authority:

- event intake exists
- task failure and lease recovery exist
- node/capability registry exists
- voice authorization policy stub exists
- auto-ingest memory candidate event handling exists
- graph constraints and indexes exist
- tests document expected behavior

The next pass should focus on hardening, replacing bootstrap shims with direct integration, protecting the new routes, and preparing Sophia/auto-ingest producers to publish events reliably.
