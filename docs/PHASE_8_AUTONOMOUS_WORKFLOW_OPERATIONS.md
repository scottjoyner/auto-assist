# Phase 8: Autonomous Workflow Operations Proposal

Date: May 24, 2026  
Owner: AssistX orchestration + ops

## 1. Objective

Phase 8 turns Orchestration V3 into a continuously operating workflow system
that can run multiple complex workflows concurrently with bounded risk,
predictable SLOs, and low operator fatigue.

---

## 2. Outcomes

1. Multi-workflow concurrency with admission control.
2. Autonomous repair/replan loops with strict guardrails.
3. Workload-aware routing across devices and model lanes.
4. Policy-aware human escalation only when required.
5. Full operations posture (SLO, alerting, recovery playbooks).

---

## 3. Operating Model

```text
Workflow Intake
  -> Admission Controller (capacity + risk + priority)
      -> Workflow Scheduler (queue classes)
          -> Step Executor Router (device/model/tool lane)
              -> Verification + Acceptance Gate
                  -> Auto-Repair/Replan loop or Human Escalation
                      -> Completion + Artifact Publish + Audit
```

### Queue classes

- `interactive`: user-facing low-latency workflows.
- `batch`: background enrichment/reporting workloads.
- `critical`: policy-sensitive or production-affecting workflows.

---

## 4. Key Design Decisions

1. Budgeted autonomy:
   - each workflow has a token/time/retry budget envelope.
2. Admission control first:
   - no workflow starts without capacity and budget availability.
3. SLA partitioning:
   - separate SLOs for interactive vs batch workloads.
4. Escalation hierarchy:
   - auto-repair -> stronger model escalation -> human review.
5. Backpressure behavior:
   - gracefully degrade low-priority queues before critical paths.

---

## 5. Phase 8 Workstreams

## 5.1 Workstream A: Scheduler + admission control

1. Add workflow scheduler service loop.
2. Implement weighted priority queue with fairness.
3. Enforce:
   - max concurrent workflows global;
   - max per-device active steps;
   - max per-queue latency budget.
4. Add pausing/draining controls for maintenance windows.

## 5.2 Workstream B: Autonomous repair and replanning

1. Add standardized failure taxonomy (`tool_error`, `policy_denied`, `timeout`, `context_miss`, `verification_fail`).
2. Configure retry policies by failure type.
3. Add plan mutation strategies:
   - reduce scope,
   - split into smaller steps,
   - switch model lane,
   - route to alternate device/toolset.
4. Dead-letter unresolved workflows into review queue with evidence bundle.

## 5.3 Workstream C: Device and model capacity economics

1. Integrate device load signals (`current_load`, queue depth, failure rate).
2. Add model lane budgets:
   - max escalation percentage per hour;
   - per-lane token/time budget thresholds.
3. Add spillover controls:
   - if local draft lane saturated -> secondary provider draft lane;
   - if escalation lane unavailable -> hold + notify, do not silently degrade policy.

## 5.4 Workstream D: SLO and alerting

1. Add SLOs:
   - workflow start latency,
   - step completion latency,
   - workflow completion success,
   - review escalation rate,
   - autonomous repair success rate.
2. Add burn-rate alerts:
   - queue growth acceleration,
   - retry storm detection,
   - escalation budget overrun.
3. Extend `/api/ops/status` and metrics exports with workflow SLO blocks.

## 5.5 Workstream E: Human-in-the-loop efficiency

1. Add review batching and smart triage ordering (risk + urgency + user impact).
2. Add one-click actions:
   - approve with constraints,
   - approve once and cache decision pattern,
   - route to specific device/agent class.
3. Add operator runbook panels:
   - “top blocked workflows”,
   - “highest retry workflows”,
   - “escalation hotspots”.

---

## 6. APIs and Data Model Additions

## 6.1 APIs

- `GET /api/workflows/queue`
- `GET /api/workflows/slo`
- `POST /api/workflows/{id}/replan`
- `POST /api/workflows/{id}/budget/update`
- `POST /api/workflows/control` (`drain`, `resume`, `set_limits`)

## 6.2 Graph entities

- `WorkflowBudget`
- `WorkflowSLOSnapshot`
- `WorkflowIncident`
- `WorkflowEscalation`

---

## 7. Rollout Strategy

1. Shadow mode:
   - scheduler decisions logged, not enforced.
2. Limited enforcement:
   - admission control on `batch` queue only.
3. Full enforcement:
   - include `interactive` queue once latency SLOs pass.
4. Critical queue activation:
   - enable only after two stable canary windows.

---

## 8. Acceptance Criteria

1. 24h canary:
   - no unbounded queue growth;
   - no retry storm;
   - escalation budget respected.
2. >= 95% admission decisions completed within scheduler SLA.
3. >= 85% autonomous repair success on retriable failures.
4. Interactive queue p95 start latency within target.
5. Operator review volume per workflow decreases versus pre-Phase-8 baseline.

---

## 9. Risks and Mitigations

- Risk: scheduler starvation for lower priorities  
  Mitigation: weighted fairness and aging.

- Risk: auto-repair loops consume excessive budget  
  Mitigation: hard per-workflow retry/token caps + dead-letter cutoff.

- Risk: noisy alerts overwhelm operators  
  Mitigation: burn-rate-based alert thresholds and grouped incident views.

---

## 10. Immediate Next Steps

1. Define queue-class SLO targets and initial budget defaults.
2. Implement scheduler skeleton with admission-control decision logs.
3. Add workflow incident taxonomy and dead-letter path.
4. Extend ops endpoint and dashboards with workflow SLO data.

---

## Implementation Status (May 24, 2026)

Completed scaffolding in code:

1. Workflow operations APIs:
- `GET /api/workflows/queue`
- `GET /api/workflows/slo`
- `POST /api/workflows/control`
- `POST /api/workflows/{workflow_id}/replan`
- `POST /api/workflows/{workflow_id}/budget/update`
- `GET /api/workflows/{workflow_id}/incidents`

2. Ops visibility:
- `/api/ops/status` now includes `workflow` block:
  - `backlog`,
  - `running`,
  - `escalation_backlog`,
  - current workflow control state.

3. Control enforcement:
- drain mode now actively blocks new claims for non-critical queue-class tasks;
- critical queue-class tasks remain claimable during drain mode.
 - admission filtering is also applied at task polling (`/api/agent/tasks`) so
   non-admissible tasks are not offered to agents in the first place.

4. Graph schema additions for workflow ops:
- `WorkflowBudget`
- `WorkflowIncident`

Remaining high-priority Phase 8 work:

1. Scheduler loop enforcement for admission decisions beyond claim-time gating.
2. Weighted fairness and queue aging.
3. Automated dead-letter routing for exhausted retry budgets.
4. Burn-rate style workflow alerts and incident grouping.
