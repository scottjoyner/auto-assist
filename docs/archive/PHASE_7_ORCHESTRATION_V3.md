# Phase 7: Orchestration V3 Design Proposal

Date: May 24, 2026  
Owner: AssistX orchestration

## 1. Objective

Build an efficient Orchestration V3 that:

- expands beyond linear intent -> task flows into multi-step workflow graphs;
- uses small locally-registered/provider models for drafting and decomposition;
- escalates to stronger models only when confidence/risk gates require it;
- improves throughput, cost efficiency, and reliability under multi-device load.

---

## 2. Core V3 Design Decisions

1. Model-tiered orchestration by default.
2. Draft-first planning with small models, verify/escalate on demand.
3. Workflow graph execution (not just task tree generation).
4. Policy-driven gating integrated with existing `policy_action`.
5. Durable traceability in Neo4j for every plan/replan/escalation decision.

---

## 3. V3 Runtime Architecture

```text
Intent Intake
  -> Classification + Outcome + Policy (existing Phase 7 baseline)
      -> Planner Router (select model lane + workflow template)
          -> Draft Plan (small model)
              -> Plan Verifier (rules + confidence + optional model check)
                  -> Workflow Graph (DAG) materialization
                      -> Step Executor Router
                          -> Hermes / QA pipeline / tool runners
                              -> Evidence + Metrics + Memory writes
                                  -> Replan loop (if step fails / context changes)
```

### Lanes

- `draft_lane` (small models): decomposition, context shaping, first-pass code/task drafting.
- `verify_lane` (small/medium): schema checks, acceptance criteria sanity, policy checks.
- `escalation_lane` (large): only for low-confidence plans, repeated failures, or high-risk actions.

---

## 4. Local Model Registry and Provider Strategy

V3 should resolve models from a local registry rather than hardcoded env defaults.

## 4.1 Registry goals

- unify local Ollama + other locally registered providers;
- track model capability metadata (`reasoning`, `coding`, `tool_use`, `latency`, `cost_tier`);
- support health and circuit-breaker status per model/provider;
- route by policy, not by raw model name.

## 4.2 Proposed registry shape

`ModelEndpoint` (config/runtime object):

- `provider`: `ollama|openai|anthropic|openrouter|custom`
- `model`: provider model id
- `lane_capabilities`: `draft|verify|escalate|code|analysis`
- `priority`: integer for ordered fallback
- `max_tokens`, `timeout_s`
- `health`: `healthy|degraded|down`
- `circuit_open_until_ts`

## 4.3 Routing policy

- For draft tasks: choose cheapest healthy model with `draft` capability.
- For verify tasks: prefer deterministic/smaller model with `verify`.
- For escalation: select strongest healthy model with `escalate`.
- Fallback chain is lane-scoped (not global shared list).

---

## 5. Workflow Graph (V3) vs Current Tree

Current orchestrator mostly emits deliverable/epic/story/task trees.  
V3 should materialize a workflow DAG with explicit dependencies and retries.

### Proposed node semantics

- `Workflow`: orchestration envelope for one intent/deliverable.
- `WorkflowStep`: executable unit with typed role:
  - `plan`, `retrieve`, `draft`, `verify`, `execute`, `review`, `publish`.
- `WorkflowDecision`: policy/routing/escalation decisions.

### Proposed relationships

- `(:Intent)-[:CREATED_WORKFLOW]->(:Workflow)`
- `(:Workflow)-[:HAS_STEP]->(:WorkflowStep)`
- `(:WorkflowStep)-[:DEPENDS_ON]->(:WorkflowStep)`
- `(:WorkflowStep)-[:LED_TO]->(:WorkflowDecision)`
- `(:WorkflowStep)-[:MATERIALIZED_TASK]->(:Task)`

---

## 6. Confidence and Escalation Policy

V3 uses confidence from multiple signals:

- intent confidence (already added);
- plan structural quality (required fields, acceptance completeness);
- execution feedback (retry count, tool failures, policy denials);
- step risk profile (`safe|caution|high_risk`).

Escalate when any of:

- draft confidence below threshold;
- 2+ repair/replan cycles on same step;
- high-risk action requested;
- policy requires human review.

---

## 7. Complex Workflow Patterns to Support

1. Research -> Compare -> Propose -> Implement -> Validate.
2. Multi-artifact builds (code + docs + rollout note + canary checklist).
3. Conditional branches:
   - if validation fails -> repair branch;
   - if policy uncertain -> human review branch.
4. Parallel branches for independent substeps, then merge.

---

## 8. Implementation Plan (Phase 7)

## 8.1 Sprint A: Foundation

1. Add `model_registry.py` and provider adapters (start with current Ollama adapter + extension hooks).
2. Add `llm_router.py` with lane-based selection and fallback.
3. Add orchestration config knobs:
   - `ORCH_V3_ENABLED`
   - `ORCH_V3_DRAFT_LANE`
   - `ORCH_V3_VERIFY_LANE`
   - `ORCH_V3_ESCALATE_LANE`

## 8.2 Sprint B: Workflow graph persistence

1. Add `Workflow`, `WorkflowStep`, `WorkflowDecision` schema + indexes in `Neo4jClient.ensure_schema`.
2. Add helper methods:
   - `create_workflow_for_intent`
   - `create_workflow_step`
   - `complete_workflow_step`
   - `record_workflow_decision`

## 8.3 Sprint C: Orchestrator V3 loop

1. Add `intent_orchestrator_v3.py` behind feature flag.
2. Implement draft -> verify -> materialize tasks pipeline.
3. Integrate current review queue (`policy_action`) as first-class branch.

## 8.4 Sprint D: Observability and controls

1. Add metrics:
   - lane usage counts
   - escalation rate
   - plan rejection rate
   - workflow completion latency
2. Add `/api/ops/status` V3 block:
   - workflow backlog
   - escalation backlog
   - draft->execute conversion rate

## 8.5 Sprint E: Progressive rollout and guardrails

1. Feature-flag rollout levels:
   - `ORCH_V3_ENABLED=0` (off)
   - `ORCH_V3_MODE=shadow` (evaluate/rank decisions, no execution impact)
   - `ORCH_V3_MODE=assist` (V3 plans; V2 still executes)
   - `ORCH_V3_MODE=primary` (V3 owns planning + execution routing)
2. Scope rollout cohorts:
   - by intent source (`voice`, `ui`, `webhook`);
   - by workflow complexity (`simple`, `complex`);
   - by selected device pool.
3. Add automatic fallback to V2 when:
   - model lane unavailable;
   - verifier fails N times;
   - workflow graph materialization fails.

## 8.6 Sprint F: Complex workflow execution features

1. Branching strategies:
   - `repair_branch` for failed verify/execute steps;
   - `review_branch` for policy/ambiguity escalations;
   - `publish_branch` for artifact and summary delivery.
2. Parallelization:
   - execute independent `WorkflowStep`s in parallel when no dependency edge exists.
3. Join/merge behavior:
   - explicit `merge` step that waits for parent branch completion and validates all outputs.
4. Workflow-level retry policies:
   - step retry budget;
   - branch retry budget;
   - total workflow retry budget with dead-letter handoff.

## 8.7 Sprint G: Data contracts and APIs

1. Add v3 API surfaces:
   - `POST /api/workflows` (manual bootstrap/testing)
   - `GET /api/workflows`
   - `GET /api/workflows/{workflow_id}`
   - `POST /api/workflows/{workflow_id}/control` (`pause|resume|cancel|retry_step`)
2. Extend existing endpoints:
   - `/api/intents` includes `workflow_id` when v3 path is active.
   - `/api/ops/status` includes v3 workflow health block.
3. Schema compatibility:
   - all v3 nodes retain stable ids and timestamps for command-center queries.

## 8.8 Sprint H: Evaluation harness

1. Build deterministic replay harness from recorded intents.
2. Compare V2 vs V3 on:
   - first-pass success,
   - total retries,
   - completion latency,
   - review load generated,
   - model-token usage by lane.
3. Promote V3 rollout only after passing SLO thresholds across replay and canary.

---

## 9. Initial Acceptance Criteria

1. At least 70% of planning actions run on draft lane models.
2. Escalation rate stays below 25% on normal workloads.
3. Complex intents produce valid workflow DAGs with dependency edges.
4. Full workflow trace visible for one intent from draft to completion.
5. No regression to existing review queue and task execution paths.

## 9.1 Expanded acceptance gates

1. Draft lane utilization:
   - >= 70% of plan-generation calls use draft lane.
2. Escalation discipline:
   - <= 25% escalation on steady-state canary workloads.
3. Quality:
   - >= 90% workflow graphs pass verifier without manual correction.
4. Reliability:
   - <= 2% workflow materialization failures.
5. Operator burden:
   - review queue growth remains stable during 24h canary.
6. Recovery:
   - fallback to V2 succeeds automatically within one orchestration cycle.

---

## 10. Risks and Mitigations

- Risk: small model drafting quality variance  
  Mitigation: strict verifier and cheap retry before escalation.

- Risk: provider heterogeneity  
  Mitigation: normalized adapter interface and lane capability tags.

- Risk: graph complexity growth  
  Mitigation: workflow retention policy and archived summaries.

---

## 11. Immediate Next Build Items

### Workstream A: Model routing
1. Create `src/assistx/model_registry.py` (registry + health state).
2. Create `src/assistx/llm_router.py` (lane routing + lane fallbacks).
3. Add `ORCH_V3_*` env configs and defaults.

### Workstream B: Graph persistence
4. Add v3 schema extensions for workflow nodes and edges.
5. Add Neo4jClient workflow methods (`create_workflow_*`, `record_decision_*`, `complete_step_*`).

### Workstream C: Orchestrator runtime
6. Add `intent_orchestrator_v3.py` skeleton + feature-flag dispatch.
7. Add shadow-mode compare logging against V2 decisions.

### Workstream D: Operations and UI
8. Add `/api/workflows*` read/control endpoints.
9. Add command-center workflow panel (backlog, blocked steps, escalations).

### Workstream E: Validation
10. Add v3 unit/integration tests and replay benchmark fixtures.
