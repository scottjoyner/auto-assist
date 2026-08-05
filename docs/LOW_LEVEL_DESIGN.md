# AssistX Low-Level Design

## Document status

- **System:** AssistX
- **Scope:** Current implementation structure, durable state, interfaces, control loops, and failure behavior
- **Companion:** [`HIGH_LEVEL_DESIGN.md`](HIGH_LEVEL_DESIGN.md)
- **Implementation authority:** Source code, Neo4j constraints, Pydantic models, and route handlers override this document when they differ.

## 1. Source layout

The implementation is organized under `src/assistx/` with API, graph, control-loop, worker, recovery, and improvement modules. The primary modules are:

| Area | Primary modules |
|---|---|
| HTTP API and authentication | `api.py`, `api_router.py`, `voice_routes.py`, `voice_contract.py` |
| Operator UI | `control_room.py`, `templates/control_room.html`, `static/css/control_room.css`, `static/js/control_room.js` |
| Graph persistence | `neo4j_client.py` and schema/constraint initialization paths |
| Allocation | `allocation_engine.py` |
| Controller leases | `controller_runtime.py` |
| Execution control | `execution_control.py`, worker adapters under `agents/` |
| Diagnosis and recovery | `diagnosis_engine.py`, `recovery_control.py`, `recovery_executor.py` |
| Degraded control plane | `degraded_control_plane.py`, `degraded_activation.py`, `operational_state.py`, `operational_journal.py`, `recovery_snapshot.py` |
| Improvement loop | `improvement_cycle.py`, `improvement_runtime.py` |
| KV-cache metadata | `kv_cache.py` |
| Readiness and dependency gates | `operations_readiness.py` and reconciliation validators under `scripts/` |

Large API files expose compatibility routes, but new behavior should be implemented in focused modules and registered through the canonical router boundary.

## 2. Runtime processes

A typical deployment contains:

1. **AssistX API** — authenticated FastAPI application and durable state coordinator.
2. **AssistX worker or direct Hermes adapter** — claims and executes eligible tasks.
3. **Neo4j** — durable graph and audit authority.
4. **Redis** — queue transport and ephemeral coordination where configured.
5. **Auto-router** — optional strict-offline OpenAI-compatible gateway.
6. **Local model runtimes** — LM Studio, llama.cpp, or other approved private runtimes.
7. **Optional degraded standby** — inert API, Redis/FalkorDB hot state, journal, and recovery services.

No process other than AssistX may create an independent canonical assignment or final task outcome.

## 3. Durable graph model

### 3.1 Core entities

| Entity | Key responsibilities |
|---|---|
| `Task` | Lifecycle, title/type, priority, required capabilities, target node, reservation, claim, lease, checkpoint, migration count, final outcome |
| `SignalEvent` / event records | Normalized intake and provenance |
| `TraceGroup`, `TraceEvent` | Correlation across intake, intent, task, dispatch, route, and completion |
| `SwarmNode` | Physical node identity, health, capabilities, block/drain/quarantine state |
| `ModelEndpoint`, `Model` | Runtime endpoint observation and served model identity |
| `AllocationReservation` | Atomic task/node/model/cache choice, snapshot revision, expiry, state |
| `ControllerLease` | Controller name, owner, expiry, monotonic fencing token |
| `ControllerCheckpoint` | Tick key, status, attempt, timestamps, result/error |
| `TaskCheckpoint` | Versioned bounded resume payload and source attempt identity |
| `TaskMigrationEvent` | Source, destination, reason, checkpoint version, status, audit |
| Recovery proposal/run/audit entities | Fingerprint, approval, runbook, execution, verification, rollback |
| `ImprovementAttempt` | Contract, base SHA, observed paths/diff, verification, signature, promotion state |
| `AgentSkillProfile` | Smoothed verified reliability by model/agent/task family |
| `PromptPrefix`, `KVCacheManifest` | Opaque prefix identity, compatibility, residency, expiry, telemetry |

### 3.2 Identity rules

- Node IDs identify physical machines, not URLs.
- Runtime IDs identify a serving process or physical runtime instance.
- Access paths are ordered private URLs attached to a runtime.
- Model instance identity includes artifact, quantization, runtime, tokenizer/template compatibility, and loaded-process provenance.
- A logical alias may resolve to multiple candidates but is never physical identity.
- Capacity is attached to the physical runtime and shared across all access paths.

### 3.3 Constraint expectations

Initialization must create uniqueness constraints for durable IDs and singleton control records. Transactional write paths must fail when constraints required for fencing cannot be established. Schema migration is operator-visible and should not silently degrade into best-effort merges.

## 4. Task state machine

The exact enum names in code are authoritative. The expected lifecycle is:

```text
PROPOSED
   |
   +-- approval --> READY
                      |
                      +-- reserve/claim --> CLAIMED --> RUNNING
                                                   |       |
                                                   |       +--> DONE
                                                   |       +--> FAILED
                                                   |       +--> PAUSING
                                                   |               |
                                                   |               +--> PAUSED
                                                   |                        |
                                                   +<---- requeue/migrate --+
```

### 4.1 Invariants

- Only approved work enters `READY` when policy requires approval.
- A live reservation may restrict claim to one node/model.
- Claim creates a new unique `claim_id` or equivalent attempt identity.
- Heartbeat, checkpoint, failure, and completion require the current claim identity.
- A reclaimed or migrated task invalidates all writes from the old attempt.
- Finalization is idempotent and must not create duplicate durable outcomes.
- Non-preemptible tasks cannot enter the checkpoint migration path.

## 5. Intake and voice authorization

### 5.1 Canonical voice request handling

1. Read the exact raw request body.
2. Verify `X-Voice-Signature` against the configured secret.
3. Parse the compact Sophia payload or richer event-envelope-compatible payload.
4. Normalize authentication state:
   - `authenticated_scott`
   - `unknown_speaker`
   - `registered_user_unverified`
   - `admin_voice_override`
   - `rejected`
5. Normalize legacy states during the compatibility window.
6. Derive an authorization decision before creating executable work.
7. Persist actor, correlation, links, transport, and policy action through all derived records.

### 5.2 Authorization behavior

| State | Executable task | Cancellation | Review/audit behavior |
|---|---|---|---|
| `authenticated_scott` | Allowed subject to task policy | Allowed only for explicit linked target | Normal trace and audit |
| `admin_voice_override` | Allowed with override provenance | Allowed only for explicit linked target | Override recorded |
| `unknown_speaker` | Not allowed | Not allowed | Create deterministic review work or incident |
| `registered_user_unverified` | Not allowed | Not allowed | Create deterministic review work or incident |
| `rejected` | Not allowed | Not allowed | Audit-only signal/incident |

Missing identity must not default to a trusted actor.

## 6. Allocation and reservation

### 6.1 Candidate filtering

A node/model candidate is eligible only when all required facts are current and acceptable:

- node is healthy and not blocked, quarantined, or drained;
- runtime identity is known;
- model is observed as loaded or explicitly admitted by current projection;
- required task capabilities are present;
- capacity is known and positive;
- access path and runtime evidence are fresh;
- policy permits the model/runtime class;
- reservation and queue pressure do not exceed configured limits.

### 6.2 Scoring

The allocation engine may combine normalized evidence for:

- expected output quality;
- throughput and time-to-first-token;
- reliability and recent error rate;
- queue depth and active load;
- task urgency and priority;
- energy or monetary cost;
- model/context compatibility;
- opportunity cost of occupying a stronger model;
- compatible local or restorable KV-cache benefit.

The score is advisory until reservation succeeds.

### 6.3 Atomic reservation

Reservation transaction inputs include:

```text
task_id
node_id
runtime_instance_id
model_instance_id
optional cache_manifest_id
snapshot/revision
expires_at
operator or allocator provenance
```

The transaction must verify current task eligibility, snapshot freshness, candidate health/capacity, and absence of conflicting active reservations. On success it records one authoritative reservation. Claim enforcement reads the current reservation inside the claim transaction.

## 7. Claim and worker protocol

### 7.1 Claim

A worker presents its node identity and asks for eligible work. The server:

1. selects a `READY` task compatible with node capability and reservation;
2. atomically changes the task to claimed/running state;
3. creates a unique claim identity and lease expiry;
4. returns task contract, approved tools, checkpoint, and execution metadata.

### 7.2 Heartbeat

Heartbeat inputs include task, worker/node, claim identity, state, progress, and optional resource observations. The server verifies the current claim before extending the lease or returning control instructions such as `PAUSING`.

### 7.3 Checkpoint

Checkpoint writes require:

- current claim identity;
- task marked preemptible;
- bounded serialized payload size;
- monotonically increasing checkpoint version;
- source attempt provenance.

Checkpoint payloads should contain only reviewed resume state, not secrets or uncontrolled binary data.

### 7.4 Completion

Completion verifies current claim identity and idempotency key, persists outcome and evidence, releases or supersedes reservations, closes traces, and updates learning records when applicable. A stale worker receives a conflict response and cannot overwrite a newer attempt.

## 8. Controller lease and reconciliation

### 8.1 Lease acquisition

A controller is identified by logical controller name and process owner. Acquisition occurs in one Neo4j transaction:

- create or read the singleton lease record;
- permit acquisition when absent, expired, or already owned by the same valid process policy;
- increment the fencing token for a new owner;
- set expiry and heartbeat metadata;
- return the token.

### 8.2 Tick execution

```text
acquire/renew lease
  -> read durable checkpoint/tick key
  -> skip completed idempotent tick
  -> mark bounded RUNNING attempt
  -> compute proposal
  -> revalidate lease owner + token
  -> commit mutations and COMPLETED checkpoint
```

If lease revalidation fails, the computed proposal is discarded. Failed ticks retain bounded error evidence and may be retried under policy.

### 8.3 Reconciliation responsibilities

Separate reconcilers may handle:

- expired claims and reservations;
- stuck preemption;
- migration targeting;
- recovery state;
- degraded journal replay;
- improvement attempt recovery;
- stale promotion locks;
- readiness and dependency observations.

Each logical loop must have its own durable tick identity and fencing scope.

## 9. Preemption and migration

### 9.1 Request sequence

1. Validate `preemptible=true` and remaining migration budget.
2. Validate a destination is healthy and satisfies all required capabilities.
3. Mark task `PAUSING` with reason and requested destination.
4. Current worker observes the request through heartbeat.
5. Worker writes checkpoint and releases claim.
6. Reconciler validates the checkpoint and supersedes source reservation.
7. Task returns to `READY` targeted to the destination.
8. Destination claims with a new claim ID and resumes from checkpoint.

### 9.2 Timeout behavior

- If the source remains healthy and does not checkpoint before the bounded deadline, policy may cancel preemption and resume the source.
- If the source lease expires, the task may return to `READY` using the latest valid checkpoint.
- Same-node migration is rejected.
- `max_migrations` prevents indefinite thrashing.

## 10. Recovery subsystem

### 10.1 Proposal

Diagnosis consumes measured observations and emits a typed recommendation containing target, action type, parameters, prerequisites, verification, rollback, and an immutable fingerprint.

### 10.2 Approval and signing

Approval records the exact fingerprint, operator identity, reason, and expiry. A trusted controller transforms the approved proposal into a signed runbook containing:

- runbook ID and nonce;
- target node;
- key ID;
- issuance and expiry;
- typed allowlisted steps;
- verification checks;
- rollback steps;
- proposal fingerprint.

The API process must not possess broader signing or execution authority than required.

### 10.3 Node execution

The node agent verifies signature, nonce, expiry, target identity, action types, and local alias allowlists. Supported adapters are explicit implementations such as systemd, launchd, Docker Compose, observation, or other reviewed typed adapters. Arbitrary shell strings are rejected by default.

### 10.4 Result

Execution records step evidence, verification, rollback attempt, final status, and audit metadata. Failed post-action health triggers rollback when defined. Uncertain state remains isolated or drained for operator inspection.

## 11. Degraded control plane

### 11.1 Standby state

The degraded standby may receive signed snapshots and heartbeats while exposing zero ordinary execution capacity. Before activation it must reject claims, provider projections, delegation, finalization, and generic task mutation.

### 11.2 Activation

Activation requires a separate signed envelope with:

- positive monotonic activation epoch;
- target cluster/standby identity;
- expiry and nonce;
- independent witness or explicit break-glass fence reference;
- verified projection/snapshot identity.

Accepted epochs persist across deactivation so an older envelope cannot reactivate the standby.

### 11.3 Operational journal

When Neo4j is unavailable, eligible degraded outcomes append to a process-safe, fsync-backed, hash-chained journal with deterministic finalization identity. Outcomes remain `PENDING_DURABLE_COMMIT`. On Neo4j return, replay uses idempotent `MERGE`-style semantics and the standby relinquishes leadership only after the journal reports zero remaining durable entries.

## 12. Repository improvement subsystem

### 12.1 Contract

An approved improvement contract defines:

- repository alias and base commit;
- allowed file paths;
- file count and diff-size budgets;
- iteration budget;
- allowed tools;
- exact verification argv arrays;
- task family and agent tier;
- approval identity and expiry.

### 12.2 Isolated execution

The executor creates a detached worktree for one task/attempt, launches the agent with a stripped environment, and prevents access to signing keys, verification keys, repository maps, or unrelated workspaces.

### 12.3 Evidence collection

The executor independently computes:

- actual changed paths;
- patch bytes and digest;
- line/file statistics;
- verification commands and outputs;
- base and resulting Git identities;
- agent/model/tool metadata;
- bounded failure reasons.

It signs the complete evidence envelope with a node-specific key. Model-reported `DONE` is not accepted without matching executor evidence.

### 12.4 Promotion

Promotion requires operator authentication and exact fingerprint. It verifies signature and digest, target repository base HEAD, clean worktree, allowed paths, and `git apply --check`; applies the patch; reruns verification; and reverses the patch if verification fails. The promotion path does not autonomously commit, push, open a PR, or release.

## 13. KV-cache metadata subsystem

AssistX stores opaque prompt-prefix and cache compatibility metadata, not raw prompt text or tensors. A cache candidate is reusable only when all required compatibility fields match, including model artifact, quantization, tokenizer, template, adapter, runtime, cache format, context settings, and privacy scope.

Allocation may compare estimated local prefill savings against transfer/restore cost. Runtime adapters must explicitly declare export/restore support. Unknown capability fails closed to affinity-only behavior.

## 14. API groups

| Area | Representative routes |
|---|---|
| Events and voice | `/api/events`, canonical voice event routes, deprecated compatibility adapter |
| Tasks | `/api/tasks/*`, task claim/heartbeat/checkpoint/preempt/migrate/fail/complete routes |
| Fleet | `/api/swarm/nodes*`, `/api/fleet/*` |
| Allocation | `/api/fleet/allocation/*` |
| Controllers | `/api/fleet/controllers` |
| Recovery | `/api/fleet/recovery-control*` |
| Improvement | `/api/fleet/improvement-cycle*` and promotion routes |
| Readiness | `/api/fleet/operations-readiness` |
| Operator UI | `/control-room` |

Route-specific request/response models in source are canonical.

## 15. Configuration and secrets

Configuration comes from environment files, deployment YAML, and explicit repository mappings. Production configuration must use separate secrets for:

- operator authentication;
- voice webhook verification;
- worker/node identity;
- recovery runbook signing and verification;
- degraded activation signing and verification;
- improvement evidence signing and verification;
- Neo4j and Redis/FalkorDB access.

Secrets must not appear in committed files, process arguments, route telemetry, evidence list responses, or model-visible environments. Secret files should be mode `0600` or stricter.

## 16. Concurrency and idempotency

- Neo4j transactions protect reservation, claim, lease, and finalization transitions.
- Monotonic fencing tokens reject stale controllers.
- Claim IDs reject stale workers.
- Nonces and signed envelope identities reject replay.
- Deterministic finalization IDs make journal replay idempotent.
- Promotion locks serialize patch application.
- API retries should use stable correlation/idempotency identities rather than creating duplicate work.

## 17. Observability

The system records:

- trace correlation across event, intent, task, dispatch, route, and result;
- controller lease and checkpoint status;
- allocation score components and rejected alternatives;
- task lease, claim, checkpoint, and migration state;
- recovery proposal, approval, runbook, verification, and rollback evidence;
- improvement attempt, signature, verification, learning, and promotion state;
- runtime/model/path/capacity freshness;
- dependency readiness and degraded journal depth.

Prometheus metrics and the control room are projections over this state. They must not mutate canonical state without authenticated API calls.

## 18. Test strategy

### Unit and contract tests

- Pydantic input and authorization decisions;
- Neo4j query behavior through fakes or disposable database fixtures;
- claim, reservation, controller, and migration fencing;
- signed runbook and evidence tamper rejection;
- improvement path/diff/tool budgets;
- KV compatibility and allocation economics;
- degraded activation and journal replay;
- operator route authentication and UI contracts.

### Integration canaries

- disposable Neo4j lifecycle canary;
- signed recovery and rollback sequence;
- stale claim and stale controller rejection;
- checkpoint and destination migration;
- degraded activation, pending durable commit, replay, and relinquishment;
- exact-fingerprint improvement promotion with failed-verification reversal.

### Physical gates

Repository CI does not prove live node identity, network paths, power interruption, real model process cleanup, thermal behavior, storage pressure, backup restore, or production rollback. These remain separate operator evidence requirements.

## 19. Change rules

Any change that introduces a new source of assignment, execution, recovery, or promotion authority must update both HLD and LLD and receive explicit architectural review. Changes to task states, graph identities, signed envelopes, route contracts, or fencing semantics require migration notes and regression coverage.
