# AssistX architecture

## Purpose

AssistX is the durable control plane between work intake and a heterogeneous
fleet of local or brokered agents. It keeps scheduling, execution, recovery,
and learning decisions observable and fenced even when nodes are slow, models
vary in quality, or a controller restarts.

The system separates four kinds of authority:

1. **State authority** — Neo4j owns durable task and control-plane state.
2. **Scheduling authority** — the allocator proposes and reserves a specific
   node/model against a recent snapshot.
3. **Execution authority** — a worker must own the current claim ID to mutate
   execution state.
4. **Mutation authority** — recovery and repository changes require typed,
   signed evidence and explicit operator gates.

## System shape

```text
              voice / API / plans / operator
                           |
                           v
                FastAPI control plane
                 |        |        |
                 v        v        v
              Neo4j   Operations  outbox
                 |
       +---------+----------+------------------+
       |                    |                  |
       v                    v                  v
  allocation          reconciliation      diagnosis
  reservation         controller leases   recovery proposal
       |                    |                  |
       +----------+---------+                  |
                  v                            v
          Paperclip or direct Hermes       signed runbook
                  |                            |
                  v                            v
       fenced claim / checkpoint       allowlisted node adapter
                  |
         +--------+----------------+
         |                         |
         v                         v
  ordinary task result     bounded code-change evidence
                                   |
                            central acceptance
                                   |
                      profile update / repair proposal
                                   |
                         exact operator promotion
```

## Durable graph

The `assistx` Neo4j database is the control-plane source of truth. Important
records include:

| Record | Responsibility |
|---|---|
| `Task` | Lifecycle, target, claim, lease, checkpoint, migration budget |
| `SwarmNode` | Identity, health, capabilities, block/drain state |
| `ModelEndpoint` / `Model` | Endpoint inventory and actually served models |
| `AllocationReservation` | Atomic node/model choice with snapshot and expiry |
| `ControllerLease` | Current reconciler leader and fencing token |
| `ControllerCheckpoint` | Replay-safe tick state and result |
| `TaskMigrationEvent` | Preemption and migration audit history |
| Recovery proposal/audit records | Approval, execution, verification, rollback |
| `ImprovementAttempt` | Signed evidence and promotion state |
| `AgentSkillProfile` | Verified performance by agent/model/task family |
| `TraceGroup` / `TraceEvent` | Cross-service correlation and provenance |

Historical long-term memory may live in a separate Neo4j database, but control
decisions must not silently depend on an unversioned external memory record.

## Task execution

### Allocation and reservation

The allocator filters for capability, node health, block/drain state, model
availability, capacity, and policy. It then compares expected quality,
latency, reliability, energy/cost, queue pressure, and the opportunity cost of
occupying a stronger model.

A recommendation is advisory until
`POST /api/fleet/allocation/reservations` atomically reserves the task,
node, model, recent snapshot revision, and TTL. Claiming enforces the live
reservation, preventing another node from taking the task. A reservation can
be explicitly released before claim.

### Claim fencing

The successful claimant receives a unique claim ID. Heartbeat, checkpoint, and
completion operations must present that current ID. Reclaims and migrations
therefore invalidate delayed messages from an earlier worker.

Typical lifecycle:

```text
PROPOSED --operator approval--> READY --claim--> CLAIMED/RUNNING
                                      |              |
                                      |              +--> DONE
                                      |              +--> FAILED
                                      |              +--> PAUSING
                                      |                       |
                                      +<--reconcile/migrate---+
```

Paperclip and direct Hermes are both execution integrations. They must consume
the canonical task lifecycle rather than introduce an independent task state
authority. Deployment selection is documented in
[`EXECUTION_AUTHORITY.md`](EXECUTION_AUTHORITY.md).

## Durable reconciliation

Reconcilers use a `ControllerLease` rather than assuming one permanent API
process. Acquiring a new lease increments a fencing token. A tick result can be
committed only if the process still owns the unexpired lease with the same
token. Completed tick keys are replay-safe; failed or stale `RUNNING` ticks can
be retried within their bounded policy.

This prevents an old leader from committing a recovery or migration decision
after another replica takes over.

## Preemption and migration

Only tasks explicitly created with `preemptible=true` can be paused.

1. The operator or allocator requests preemption.
2. The owner observes `PAUSING` on a fenced heartbeat.
3. The owner writes a versioned, size-bounded checkpoint and releases the
   claim.
4. The execution reconciler validates a healthy destination with all required
   capabilities.
5. The task returns to `READY`, targeted to that destination.
6. A new claim resumes from `checkpoint_json`.

Migration is bounded by `max_migrations`. Active source reservations are
superseded atomically. A pause that is not acknowledged resumes the valid
source execution or returns to `READY` after lease expiry.

## Recovery loop

Diagnosis maps observed incidents to typed recovery recommendations. Recovery
does not execute arbitrary model-generated shell:

```text
observation -> diagnosis -> proposal -> fingerprint approval
            -> signed typed runbook -> node allowlist adapter
            -> verification -> outcome/audit -> rollback or release
```

Runbooks have a key ID, bounded lifetime, explicit steps, verification
requirements, and rollback plan. Each node verifies the signature and exposes
only configured systemd, launchd, observation, or Compose aliases. Immutable
Compose deployments require image digests.

See [`fleet-recovery-rollout.md`](fleet-recovery-rollout.md).

## Self-improvement loop

Repository work uses a distinct evidence boundary:

```text
operator proposal -> approved bounded contract -> isolated detached worktree
-> agent tool packet -> executor-measured diff and verification
-> signed evidence -> central acceptance -> skill-profile update
-> optional narrower repair proposal -> operator fingerprint promotion
```

The model cannot expand paths, tools, commands, file count, diff size, or
iteration budget. The executor, not the model, reads Git state and runs
verification. Raw patches are stored in the attempt evidence but omitted from
list responses.

Promotion revalidates the signature and patch digest, requires the original
base HEAD and a clean target, checks paths, applies the patch, reruns tests, and
reverses the patch on failed verification. Successful promotion intentionally
stops before commit or release.

See [`self-improvement-cycle.md`](self-improvement-cycle.md) and
[`self-improvement-rollout.md`](self-improvement-rollout.md).

## Operations workspace

`GET /operations` serves the authenticated fleet workspace. Its APIs expose:

- real node and loaded-model inventory;
- allocation recommendations and reservations;
- controller lease/checkpoint health;
- readiness gates without secret values;
- incident, diagnosis, recovery, maintenance, and quarantine state;
- checkpoint and migration progress;
- learning profiles, accepted attempts, and patch promotion controls.

The UI is an operator surface, not a second source of truth. Every mutating
control goes through an authenticated API and durable audit state.

## Primary API groups

| Area | Routes |
|---|---|
| Intake and traces | `/api/events`, `/api/voice/events`, `/api/traces/*` |
| Tasks and workers | `/api/tasks/*`, `/api/agent/tasks` |
| Fleet inventory | `/api/swarm/nodes`, `/api/fleet/*` |
| Allocation | `/api/fleet/allocation/*` |
| Controllers | `/api/fleet/controllers` |
| Recovery | `/api/fleet/recovery-control*` |
| Migration | `/api/tasks/{id}/checkpoint`, `/preempt`, `/migrate`, `/api/fleet/migrations` |
| Improvement | `/api/fleet/improvement-cycle*` |
| Readiness | `/api/fleet/operations-readiness` |

Route schemas in `src/assistx/api.py` are authoritative when this summary and
the running API differ.

## Trust boundaries

- Basic or trusted-header authentication protects operator endpoints.
- Node tokens identify recovery-capable fleet agents.
- Runbook HMAC keys authorize typed recovery instructions.
- Separate node-specific improvement HMAC keys attest executor evidence.
- Signing secrets are removed from the model subprocess environment.
- Verification commands are argv arrays executed with `shell=False`.
- Repository aliases resolve only through `ASSISTX_REPOSITORY_ROOTS_JSON`.
- Unsafe legacy command payloads remain an explicit opt-in and should stay off.
- Secrets, raw patches, and unrestricted process environments do not belong in
  list/status responses.

## Failure behavior

The design prefers a visible stop over an ambiguous mutation:

- stale reservation snapshot: reject and replan;
- wrong node claim: reject;
- stale claim or controller token: reject;
- missing checkpoint acknowledgment: bounded resume/requeue;
- invalid runbook signature or alias: reject;
- failed recovery verification: rollback or retain drain for inspection;
- missing/invalid improvement evidence: mark attempt failed;
- repository HEAD drift or dirty promotion target: reject;
- failed post-apply verification: reverse the patch;
- uncertain release state: leave the change uncommitted for an operator.
