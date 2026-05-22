# Sophia → AssistX Neo4j Memory Migration Plan

## 1) Goal and Outcome

This migration introduces **Sophia project data** as a first-class input stream for AssistX, so that:

1. Sophia event/context data is ingested into this repo’s Neo4j memory graph.
2. “Quick input” requests from phone clients are accepted and resolved against that memory.
3. An agentic loop (Hermes-style) can reason over near-real-time context and execute safe actions.
4. Dashboard / AI command center surfaces live memory state, agent runs, and outcomes.

This document is intended for architecture approval and sequencing of large integration work.

---

## 2) Current Baseline in This Repo

### Data + execution baseline
- AssistX already stores conversations, summaries, tasks, agent runs, tool calls, artifacts, transcriptions, and segments in Neo4j.
- AssistX has an orchestrated execution loop with tool gating/policy and run provenance.
- API/UI layer already supports task review/approval and async answering patterns.

### What this means for migration
We should **extend**, not replace, the current model:
- Keep existing Task/AgentRun/ToolCall provenance.
- Add Sophia-specific domain entities + edges.
- Reuse current orchestration entry points for “quick input” while adding low-latency path and memory retrieval strategy.

---

## 3) Target Architecture (High-Level)

```text
Sophia Git Project (events, state, metadata)
  └── Sophia Adapter (extract/normalize/version)
      └── Ingestion Gateway (idempotent upsert + validation)
          └── Neo4j Memory Graph (AssistX + Sophia domain)
              ├── Retrieval Layer (subgraph/query templates)
              ├── Hermes Agentic Loop (plan→act→observe→reflect)
              └── API for Quick Inputs (phone/web)
                    └── Dashboard + AI Command Center
```

### Core principles
- **Schema evolution, not schema explosion**: introduce bounded labels/relationships.
- **Idempotent ingestion**: deterministic natural keys + upsert semantics.
- **Operational traceability**: every decision/tool/action linked to memory snapshot.
- **Safety gates**: policy and approval remain in-loop for high-risk actions.

---

## 4) What We Need from the Sophia Git Repo (Evaluation Checklist)

Before implementation, we need a structured intake from Sophia.

## 4.1 Repo-level details
- Canonical repo URL + default branch.
- Deployment mode (library, service, jobs, monorepo packages).
- Runtime/language versions.
- Existing CI/test coverage and fixtures.

## 4.2 Data contract details
- Source entities (e.g., sessions, contacts, intents, memory items, events).
- Field definitions, optionality, enums.
- IDs and natural keys (global uniqueness guarantees).
- Temporal semantics (`created_at`, `updated_at`, event ordering, timezone).
- Deletion semantics (hard-delete, soft-delete, tombstones).

## 4.3 Change data and sync model
- Pull vs push integration.
- Snapshot vs incremental/event stream.
- Backfill window expectations and replay rules.
- Rate limits and pagination shape.

## 4.4 Semantics and decisioning
- How Sophia represents confidence, relevance, recency.
- Existing ranking/retrieval logic.
- Any “memory decay” or retention rules.

## 4.5 Security/compliance
- PII classes present.
- Encryption/signing requirements.
- Access token lifecycle.
- Audit requirements and redaction expectations.

## 4.6 Operational SLOs
- Expected ingest throughput.
- Max tolerated staleness for phone quick inputs.
- Availability targets.
- Incident ownership and runbook location.

---

## 5) Proposed Data Model Extension in Neo4j

Add Sophia-compatible domain nodes while preserving AssistX run tracking.

## 5.1 New node labels (proposed)
- `SophiaSource` (source system identity/version)
- `SophiaEntity` (generic external object envelope)
- `MemoryItem` (normalized memory fact/note/context)
- `SignalEvent` (time-ordered events from Sophia)
- `ClientSession` (phone/web session correlation)
- `UserIntent` (quick input interpretation)

## 5.2 New relationships (proposed)
- `(:SophiaSource)-[:EMITS]->(:SignalEvent)`
- `(:SignalEvent)-[:UPDATES]->(:MemoryItem)`
- `(:ClientSession)-[:SUBMITTED]->(:UserIntent)`
- `(:UserIntent)-[:USES_CONTEXT]->(:MemoryItem)`
- `(:UserIntent)-[:TRIGGERED_RUN]->(:AgentRun)`

## 5.3 Key design constraints
- External IDs mapped to `external_id` + `source` composite uniqueness.
- Immutable event log pattern for `SignalEvent` + derived current-state `MemoryItem`.
- All agent outcomes continue linking through existing `AgentRun`, `ToolCall`, `Artifact`.

---

## 6) Quick Input (Phone) Flow Design

## 6.1 Ingress contract
`POST /api/quick-input`
- Required: `session_id`, `text`, `client_ts`, `idempotency_key`.
- Optional: `geo`, `device_context`, `channel`, `priority`.

## 6.2 Processing path
1. Validate + de-duplicate via idempotency store.
2. Create `ClientSession`/`UserIntent` nodes.
3. Retrieve relevant `MemoryItem` subgraph (recency + semantic + graph-neighbor score).
4. Run Hermes loop with bounded tool budget/time.
5. Persist run/tool traces and return response + action summary.

## 6.3 Latency target tiers
- P50: < 800 ms (read-heavy answer, no heavy tools)
- P95: < 2.5 s (one tool call path)
- Async fallback after timeout with streaming/event updates.

---

## 7) Hermes-Agentic Loop (Design for Approval)

Proposed loop stages for this repo:

1. **Observe**: build task frame from quick input + memory slice.
2. **Orient**: classify intent, risk, and confidence.
3. **Plan**: pick strategy (answer-only, retrieve+reason, execute action).
4. **Act**: execute approved tools/policies.
5. **Reflect**: evaluate output quality and consistency checks.
6. **Commit**: write outcomes + artifacts + memory updates.

Guardrails:
- Policy deny list + parameter validation remains mandatory.
- Escalate high-risk actions to `REVIEW` task status.
- Save structured “reasoning summary” (not raw chain-of-thought) for observability.

---

## 8) Migration Phases

## Phase 0 — Discovery & Contract Freeze (1–2 weeks)
- Complete Sophia intake checklist.
- Produce normalized field mapping doc.
- Define source-of-truth ownership per entity.
- Approve target schema extension.

**Exit criteria:** signed integration contract + sample payload corpus.

## Phase 1 — Adapter + Ingestion Skeleton (1–2 weeks)
- Build Sophia adapter module (`src/assistx/integrations/sophia/`).
- Implement pull/sync runner with checkpointing.
- Add idempotent upserts into staging labels.

**Exit criteria:** repeatable ingest in non-prod with zero duplicate drift.

## Phase 2 — Graph Model Activation (1 week)
- Promote staging data into target `MemoryItem`/`SignalEvent` model.
- Add constraints/indexes and migration script.
- Add schema observability checks.

**Exit criteria:** graph integrity checks pass; query performance baseline recorded.

## Phase 3 — Quick Input API + Retrieval (1–2 weeks)
- Implement `/api/quick-input` endpoint + async mode.
- Build retrieval/ranking query templates.
- Add response SLA instrumentation.

**Exit criteria:** load test hits p95 target in staging profile.

## Phase 4 — Hermes Loop Integration (1–2 weeks)
- Wire quick input tasks into orchestrator.
- Add policy profiles for phone-triggered actions.
- Persist run-grade telemetry to dashboard.

**Exit criteria:** deterministic replay for sampled sessions; safety gates verified.

## Phase 5 — Dashboard/Command Center Enhancements (1–2 weeks)
- Add panels for memory freshness, event lag, intent classes, run outcomes.
- Add drill-down trace for `UserIntent → AgentRun → ToolCall`.

**Exit criteria:** operator runbook + production readiness review.

## Phase 6 — Cutover + Stabilization (1 week)
- Canary rollout by tenant/channel.
- Backfill/replay validation.
- Incident drills and rollback test.

**Exit criteria:** cutover signoff + postmortem-free burn-in window.

---

## 9) Testing and Validation Strategy

## 9.1 Contract tests
- Validate Sophia payload versions against schema.
- Reject/park malformed or unknown-version payloads.

## 9.2 Data correctness tests
- Idempotency replay tests.
- Event ordering and late-arrival reconciliation tests.
- Graph relationship invariants.

## 9.3 Agent behavior tests
- Golden quick-input scenarios.
- Policy deny/allow boundary tests.
- Deterministic mocked tool-call tests.

## 9.4 Performance tests
- Ingest throughput soak tests.
- Quick-input latency under concurrent sessions.

## 9.5 Operational tests
- Neo4j failover/temporary unavailability behavior.
- Queue backlog and retry policy behavior.

---

## 10) Risks and Mitigations

- **Schema mismatch risk** → start with Sophia staging envelope and explicit mapping layer.
- **Latency regressions** → precompute memory features and set tool-step budgets.
- **Duplicate/conflicting data** → strict upsert keys + replay-safe checkpoints.
- **Safety gaps in autonomous actions** → enforce policy + review gate + audit trail.
- **Operational complexity** → phased rollout with canary and rollback paths.

---

## 11) Deliverables for Approval Packet

1. `MIGRATION.md` (this design).
2. Sophia integration contract (field mapping + versioning).
3. Neo4j schema change proposal (constraints/indexes/cypher).
4. Quick-input API contract + SLA targets.
5. Hermes-loop guardrail matrix.
6. Rollout runbook + rollback plan.

---

## 12) Immediate Next Actions

1. Hold a Sophia discovery session and fill Section 4 checklist.
2. Capture 100–500 representative Sophia records/events for mapping tests.
3. Approve Phase 0/1 scope and create implementation epics.
4. Stand up a staging Neo4j dataset dedicated to migration dry runs.

