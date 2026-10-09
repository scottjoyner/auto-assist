# DeLM-inspired AssistX shadow collaboration — preregistered protocol

**Date:** 2026-10-09 America/New_York  
**Status:** DESIGN / EXPERIMENT ONLY / NO PRODUCTION AUTHORITY  
**Tracker:** [#167](https://github.com/scottjoyner/auto-assist/issues/167)  
**External references:** [DeLM](https://yuzhenmao.github.io/DeLM/), [arXiv:2606.10662](https://arxiv.org/abs/2606.10662). The upstream results are motivation, **not** fleet observations.

## 1. Decision and authority boundary

Study decentralized *discovery and sharing*; do **not** install a second authoritative task queue. AssistX/Neo4j remains sole authority for tasks, inventory, allocation, claim IDs, owner/lease fencing, termination, recovery, and final outcomes, per [LLD](../LOW_LEVEL_DESIGN.md) and [execution authority](../EXECUTION_AUTHORITY.md).

Workers may publish **untrusted, append-only proposals and findings**. A separate read-only projection makes them visible to other experimental workers. A peer's discovery is not a task claim, grant, execution command, verified fact, authorized tool output, or promotion receipt. Only a policy-checked canonical AssistX state transition can grant a future worker a specific scoped task. **No task creation/claim/dispatch tool is invoked in the first slice**.

Shadow mode defaults OFF. No hosted inference, live provider admission, production API/Redis/Neo4j/NAS writes, service restarts, filesystem recovery writes, or changes to currently deployed workers. The experimental store must be disposable and outside the NAS5 recovery tree. Do not consume/kill the ongoing fine-tuning GPU workload. Raspberry Pi is excluded.

## 2. Preregistered questions and predictions (not observations)

**Primary comparison** is two-worker centralized relay (B) versus two-worker shared-findings (C), with identical workload, context budget, approved model loadout, timeout and available physical resources.

- **H1 latency:** C median paired wall-clock time is at least **15% lower** than B. Expected cause: less duplicate investigation and relay delay.
- **H2 correctness:** C does not lose more than **one task** versus B on the same held-out 12-task set. Every task is graded against independent test/fixture outcomes, not agent self-reports.
- **H3 safety:** C yields **zero** unauthorized state changes, wrong-task finding acceptance, stale/superseded attempt acceptance, or unaudited completion events in adversarial tests. This is a hard gate, not an optimization target.
- **H4 resource efficiency:** Total model tokens, model runtime, host-hours, peak RSS, and aggregate throughput are reported even if C is faster. C consuming >**1.30x** total compute versus B is classified as *latency gain with resource regression*, not an unqualified win.
- **H5 interpretation:** One-worker arm A contextualizes parallel scaling but **cannot** isolate DeLM-style coordination value; B vs C isolates coordination at equal worker count.

These thresholds are evaluation criteria, not claims of likely actual gain. If tasks or models are changed after observing results, report that as a new experiment.

## 3. Benchmark design

### Pre-run freeze

1. Freeze 12 held-out, non-sensitive historical coding tasks (6 bug fixes, 3 test/debug, 3 multi-file refactors), each with repository commit SHA, permitted paths, immutable public/sanitized fixtures, verifier command, timeout, and expected outcome. Keep assessment tests outside workers' inputs.
2. Establish three arms: **A** one worker, sequential; **B** two workers with centralized investigator/relay; **C** two workers sharing validated intermediate events. Use the same agent runtime and local model class wherever feasible; if not feasible, report the hardware and model differences and do not claim a pure coordination effect.
3. Use two predeclared deterministic seeds per task and arm: up to 72 paired runs. Randomize arm order within each task/seed to limit warm-cache/time-of-day bias. Record per-run CPU/GPU availability and background loads.
4. Hold identical wall-time (maximum 120 minutes), tool permissions, approved worktree paths, test commands and model token budgets; predeclare early-abort/resource ceilings separately.
5. Synthetic unit/replay negative controls run **before** any physical execution attempt. Physical shadow runs require a separate go/no-go and disposable read/write roots signed off for MacBook Air and xwing. Benchmark data may use copies of historical issues; do not use production credentials.

### Required measurements

Each run captures immutable corpus revision, arm, random seed, model/runtime digest, per-node identity, git base SHA, hardware/driver, exact start/finish times, source branch, cache condition, agent count, attempted/accepted events, reviewer/evaluator identity, completed verification and failures.

Report wall-clock p50/p95, paired B/C speedup distribution and bootstrap 95% confidence interval by task, test pass rate, number of regressions, task correctness, prompt/output tokens by worker, estimated local compute seconds, idle seconds, duplication count, investigations reused, rejected claims, context propagation delay, event journal integrity, and trace/evidence completeness. Treat timeout as timeout; no deletion of failed or slow trials.

Where possible, separate orchestration overhead from model inference and tool execution. Do not equate fewer turns with lower end-to-end cost. Publish sanitized raw per-run records and the preregistered analysis script, not solely aggregate wins.

## 4. Shadow event contract, v0 (proposed; not deployed)

A JSONL envelope in a disposable store, with each item carrying:

```json
{
  "schema": "assistx.delm.shadow-event.v0",
  "experiment_id": "fixture-exp-001",
  "task_id": "fixture-task-001",
  "attempt_id": "fixture-attempt-001",
  "event_id": "fixture-event-001",
  "parent_event_ids": [],
  "event_kind": "FINDING",
  "producer_node_id": "fixture-xwing",
  "producer_agent_id": "fixture-agent-a",
  "source_commit_sha": "<40-hex>",
  "scope_digest_sha256": "<64-hex>",
  "evidence_digest_sha256": "<64-hex>",
  "claim_generation_observed": 0,
  "monotonic_sequence": 1,
  "created_at_utc": "2026-10-09T04:00:00Z",
  "claim": "Evidence-limited finding; not executable authority",
  "verification_status": "UNVERIFIED"
}
```

Permitted `event_kind`: `FINDING`, `FAILED_ATTEMPT`, `CONTRADICTION`, `SUPERSESSION`, `ARTIFACT_REFERENCE`, `WORK_PROPOSAL`. **No `EXECUTE`, `CLAIM`, `COMPLETE`, `APPROVE`, or `PROMOTE` event** is authority-bearing. Schema validation must reject unknown kinds, malformed hashes, missing provenance, cross-task parent links, invalid sequence and duplicate event IDs with conflicting payload.

Experiment storage is append-only and hash-chained; a record can be invalidated/superseded through a new event, never overwritten. The protocol must distinguish **claim text**, **source evidence** and **verification state**. A peer may use a finding as a search hint in an already-approved bounded workspace, not as authorization to edit more files or expand scope. Store only short sanitized summaries and source/artifact hashes; never private chain-of-thought, secrets, opaque shell commands or unredacted source contents.

Worker subscription:
- Reads only events for its exact experiment/task/repository source scope.
- Checks proof of journal continuity, source commit, event status, causal links and worker exposure policy.
- Rejects events sourced from invalid/superseded attempts or lacking validated evidence when verification is required.
- Logs `seen` vs `used` vs `rejected` with source event IDs, enabling causal accounting of actual collaboration benefits.
- Does not call external commands or production APIs based on a published event.

The eventual physical distributed store/custodian remains **TBD**, and any real signed event path is subject to separate custody and failover acceptance. A Redis-only TTL or producer's self-attestation is *not* a substitute for verified physical revocation or trace durability.

## 5. Negative acceptance matrix (fixture-only first)

| Failure stimulus | Required outcome |
| --- | --- |
| Duplicate event ID and identical payload | Idempotent no-op, one counted event |
| Duplicate event ID with different payload | Reject, audit integrity error |
| Cross-task/repo evidence or parent | Reject, no information flow |
| Incorrect digest, source SHA or missing evidence | Reject or quarantine unverified, never accept as fact |
| Out-of-order or missing journal sequence | Fail closed pending reconciliation |
| Poisoned instruction inside a finding | Retain as untrusted quoted data; never run tool or change scope |
| Stale claim generation, revoked/superseded attempt | Reject adoption and refuse finalization |
| Wrong-node claim, replay, fabricated completion | No new authority; audit negative decision |
| Worker crash/restart or event delivery twice | Replay same validated projection without data loss or duplication |
| Missing trace sink / custody failure | Stop experiment, preserve available local evidence, no silent success |
| Storage unavailable/at capacity | No new writes or automatic fallback to NAS5, no execution authorization |

Test the negative controls in a clean HOME/temp directory with network disabled and inert fixture worker identities. Assertions should count accepted/rejected events and verify that no source tree, canonical task, provider or production command is mutated.

## 6. Release intersections and explicit NO-GO

This experiment is **not part of** the proposed October 15 AssistX production release and does not unblock it. Production readiness doc [PR #162](https://github.com/scottjoyner/auto-assist/pull/162) reports failing broad CI and independent credential, ingress, cancellation and custody gates. Source and physical acceptance for trace-first execution [PR #119](https://github.com/scottjoyner/auto-assist/pull/119) / [issue #120](https://github.com/scottjoyner/auto-assist/issues/120), trace archival [#127](https://github.com/scottjoyner/auto-assist/issues/127), audit completeness [#117](https://github.com/scottjoyner/auto-assist/issues/117), ingress [#149](https://github.com/scottjoyner/auto-assist/issues/149), and physical rate/cancellation [#148](https://github.com/scottjoyner/auto-assist/issues/148) remain independent.

**Smallest next code slice:** self-contained typed shadow envelopes + deterministic journal validator/projector + synthetic contradictory/replayed-event fixtures; no API imports, service integration, provider calls, or distributed claims. Produce executable tests and append an observation/results section only after those tests run. A later independent approval gates xwing/MacBook Air physical benchmarks.

## 7. Evidence ledger (initial)

| Item | Value |
| --- | --- |
| Research approval | Direction confirmed in chat 2026-10-09; scope limited to shadow architecture |
| Repo state reviewed | `main` README, LLD and execution-authority doc; PR #162 release-readiness document |
| Code or tests executed for this proposal | **None** |
| Real fleet commands or provider calls | **None** |
| Physical benchmarking outcome | **Not measured** |
| Promotion recommendation | **NO-GO** until tests, physical custody, workload comparison and operator gates are satisfied |
