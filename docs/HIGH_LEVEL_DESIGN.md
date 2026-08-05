# AssistX High-Level Design

## Document status

- **System:** AssistX
- **Purpose:** Canonical high-level design for the graph-backed offline fleet control plane
- **Audience:** Operators, maintainers, reviewers, integration owners, and implementation agents
- **Authority:** Describes the intended architecture of the current `main` branch. Executable schemas, route definitions, and state transitions in source code remain authoritative when implementation and documentation differ.

## 1. Problem statement

AssistX coordinates work across a heterogeneous fleet of local inference nodes and agent executors. The system must continue to make safe, observable decisions when nodes are intermittent, model quality varies, execution is preempted, controllers restart, or recovery actions are required.

The primary design problem is not simply task dispatch. It is maintaining one durable authority for:

- work intake and task lifecycle;
- node, runtime, model, and capacity identity;
- allocation, reservations, claims, leases, and checkpoints;
- recovery, migration, and degraded operation;
- bounded repository self-improvement;
- operator approvals and audit evidence.

AssistX must prevent routers, workers, model runtimes, or agents from becoming competing sources of truth.

## 2. Goals

1. Keep Neo4j as the durable control-plane authority.
2. Allocate work using explicit capability, health, capacity, quality, latency, reliability, and opportunity-cost evidence.
3. Fence every execution attempt so stale workers and stale controllers cannot mutate newer state.
4. Support checkpointed preemption and bounded cross-node migration.
5. Convert diagnosis into typed, signed, allowlisted recovery actions rather than arbitrary shell execution.
6. Permit bounded agent-assisted code changes while preserving operator-only promotion authority.
7. Operate entirely against approved local or private-network inference runtimes.
8. Provide an authenticated operator surface with complete provenance and failure visibility.
9. Support a warm degraded control plane without allowing it to become a second durable authority.

## 3. Non-goals

AssistX does not:

- treat `auto-router` as an allocator, inventory database, or worker scheduler;
- allow agents to approve, promote, commit, push, or release their own changes;
- accept unrestricted model-generated shell commands for recovery;
- infer physical runtime identity from an access URL alone;
- automatically admit a runtime because it responds to `/v1/models`;
- store raw prompt text, token arrays, or KV tensors in control-plane graph records;
- make production cutover decisions solely because repository CI passes.

## 4. System context

```text
voice / API / plans / operator
              |
              v
      AssistX FastAPI boundary
              |
      +-------+--------+------------------+
      |                |                  |
      v                v                  v
   Neo4j          control room       durable outbox
      |
      +----------+-----------+-------------------+
                 |           |                   |
                 v           v                   v
             allocation  reconciliation      diagnosis
                 |           |                   |
                 v           v                   v
           reservations  controller fence   recovery proposal
                 |                               |
                 v                               v
        direct Hermes/OpenCode             signed runbook
                 |                               |
                 v                               v
      claim/checkpoint/result          allowlisted node adapter
                 |
                 v
       evidence / learning / operator promotion
```

### External collaborators

| System | Relationship to AssistX |
|---|---|
| `auto-router` | Strict-offline OpenAI-compatible gateway. It normalizes and forwards requests using AssistX-approved runtime projections and does not own durable assignment. |
| `lms` | Produces signed, non-admitting observation, qualification, canary, and rollback evidence for physical runtimes and loadouts. |
| `fleet-llm-profiles` | Stores desired-state node profiles and imported signed evidence. It is not a live scheduler or lifecycle controller. |
| Hermes workers | Execute claimed work using current claim identity and return fenced heartbeat, checkpoint, result, and evidence updates. |
| Neo4j | Durable source of truth for control-plane state, audit history, approvals, and final outcomes. |
| Redis/FalkorDB | Queue or reconstructable hot operational state where enabled; never the final durable authority. |
| LM Studio / llama.cpp / other local runtimes | Serve models. They do not assign work or establish their own canonical fleet identity. |

## 5. Architectural principles

### 5.1 Single durable authority

Neo4j owns canonical tasks, inventory, reservations, controller leases, claims, checkpoints, migration events, recovery state, improvement evidence, and audit history. Other stores may cache or transport data but must be reconstructable from durable records and current observations.

### 5.2 Explicit identity separation

The design distinguishes:

- physical node;
- runtime process or runtime instance;
- private access path;
- loaded model process;
- model artifact and quantization;
- logical model alias;
- capacity slot.

Multiple URLs may refer to one runtime and therefore one shared slot pool. A URL, alias, or observer endpoint cannot create another physical runtime identity.

### 5.3 Fenced mutation

Every mutation is tied to current authority:

- reservations bind task, node, model, snapshot, and expiry;
- claims issue a unique attempt identity;
- worker heartbeats, checkpoints, and completion require the current claim;
- controllers use leased monotonic fencing tokens;
- recovery uses signed typed runbooks and node allowlists;
- code promotion requires exact patch fingerprint, base HEAD, clean target, and operator action.

### 5.4 Fail closed

Unknown, stale, conflicting, or unverifiable state blocks mutation. Examples include stale reservations, expired controller leases, wrong-node claims, invalid signatures, missing capacity, dirty promotion targets, and incomplete recovery verification.

### 5.5 Evidence before admission

Repository tests prove software contracts, not live physical state. Runtime admission requires current identity, path, completion, capacity, reliability, benchmark, and rollback evidence. Evidence is imported and reviewed separately from routing activation.

## 6. Major subsystems

### 6.1 Intake and traceability

Authenticated API and voice boundaries normalize incoming events, attach actor and correlation identity, and create durable traceable work. Untrusted or unverified voice actions become review work or audit-only events rather than executable tasks.

### 6.2 Fleet inventory and allocation

Fleet observations describe physical nodes, runtimes, loaded models, health, capacity, and approved paths. The allocator filters invalid candidates and scores eligible placements using quality, throughput, latency, reliability, load, cost, and displacement of stronger resources.

### 6.3 Reservation and execution

An allocation recommendation becomes authoritative only after an atomic reservation. A worker then claims the task and receives a unique claim ID. The worker may heartbeat, checkpoint, or finish only while it owns that claim.

### 6.4 Durable reconciliation

Reconcilers acquire a Neo4j-backed controller lease with a monotonically increasing fencing token. Tick state is checkpointed and replay-safe. A stale controller cannot commit after another controller acquires a newer token.

### 6.5 Preemption and migration

Preemptible tasks can move through `PAUSING` to a durable checkpoint and return to `READY` for a validated destination. Migration is bounded by policy, destination capability, checkpoint availability, and a maximum migration count.

### 6.6 Recovery and degraded operation

Diagnosis creates a typed recovery proposal. Approved recovery is transformed into a signed, expiring, target-specific runbook executed only through allowlisted adapters. A degraded standby remains inert until a separately signed, monotonically fenced activation is verified. Final outcomes remain pending until Neo4j accepts replay-safe durable commits.

### 6.7 Bounded self-improvement

Repository improvement begins with an operator-approved contract defining paths, tools, verification commands, and budgets. The executor uses an isolated worktree, independently measures the diff and tests, signs the evidence, and submits it for central acceptance. Promotion is a separate operator action and stops before release unless explicitly handled by another trusted workflow.

### 6.8 Operator control room

The authenticated control room presents physical runtime state, tasks, allocation evidence, dependencies, incidents, recovery, migration, learning, and promotion. The UI is a projection over API and Neo4j state, never a second authority.

## 7. Core control flows

### 7.1 Normal task flow

```text
intake
  -> durable task
  -> allocation recommendation
  -> atomic reservation
  -> node claim with claim_id
  -> execution heartbeat/checkpoint
  -> fenced completion
  -> trace and outcome audit
```

### 7.2 Runtime route flow

```text
AssistX allocation / approved runtime projection
  -> auto-router request and admission
  -> approved LAN-first or Tailscale path
  -> local runtime generation
  -> route provenance returned to AssistX
```

### 7.3 Recovery flow

```text
observation
  -> diagnosis
  -> proposal
  -> operator approval of exact fingerprint
  -> signed typed runbook
  -> target allowlist adapter
  -> verification
  -> success, rollback, or retained drain
```

### 7.4 Improvement flow

```text
approved bounded contract
  -> isolated worktree
  -> agent implementation
  -> executor-measured diff and verification
  -> signed evidence
  -> central acceptance and learning update
  -> optional operator promotion
```

## 8. Data and authority model

| Data class | Durable authority | Notes |
|---|---|---|
| Task lifecycle and final outcome | Neo4j | Claim- and controller-fenced |
| Physical inventory and approved paths | Neo4j plus signed observations | Freshness and provenance required |
| Queue transport | Redis | Reconstructable; not final truth |
| Degraded hot state | FalkorDB/Redis where enabled | Bounded and reconstructable |
| Route/admission telemetry | Router ephemeral state plus AssistX events | Cannot assign work independently |
| Qualification/canary evidence | Signed external artifacts imported into profiles/AssistX review | Non-admitting until separately approved |
| Prompt/KV metadata | Neo4j metadata and external content-addressed payloads | No raw prompts or tensors in graph |
| Repository patch evidence | Signed attempt records and external patch artifact | Promotion separately fenced |

## 9. Security boundaries

- Operator endpoints require configured authentication.
- Voice requests use raw-body signature verification and explicit identity taxonomy.
- Workers and recovery nodes use distinct node credentials.
- Recovery runbooks and implementation evidence use separate signing keys.
- Secret values are removed from model subprocess environments and status responses.
- Verification commands are reviewed argv arrays executed without a shell.
- Repository paths resolve through explicit aliases and allowlists.
- Hosted inference fallback is excluded from the reconciled deployment.

## 10. Availability and failure strategy

The system favors visible unavailability over unsafe mutation:

- stale snapshot or reservation: reject and replan;
- unknown runtime capacity: do not route;
- lost worker lease: invalidate old claim and reconcile;
- controller restart: reacquire lease and resume from durable checkpoint;
- failed checkpoint migration: resume source or return to `READY` under bounded policy;
- invalid recovery evidence: reject;
- failed post-recovery health: rollback or retain isolated state for inspection;
- Neo4j unavailable during degraded execution: journal `PENDING_DURABLE_COMMIT` and replay idempotently after recovery;
- uncertain repository promotion: leave target uncommitted and operator-visible.

## 11. Deployment model

Normal development may use Docker Compose. Production reconciliation uses an isolated side-by-side deployment with distinct containers, ports, networks, state directories, secrets, evidence, and migration ledger. Shadow readiness and rollback evidence must be reviewed before cutover. A passing ledger is evidence, not autonomous authorization.

## 12. Key architectural decisions

1. **Neo4j remains final durable authority.**
2. **Auto-router remains a narrow strict-offline gateway.**
3. **Physical identity and access-path identity are separate.**
4. **All execution and control loops are fenced.**
5. **Recovery is typed, signed, bounded, and reversible.**
6. **Agent-produced repository changes require independent evidence and operator promotion.**
7. **Degraded operation is warm but inert until separately activated.**
8. **Physical admission remains outside repository-only CI.**

## 13. Related documents

- [`LOW_LEVEL_DESIGN.md`](LOW_LEVEL_DESIGN.md)
- [`ARCHITECTURE.md`](ARCHITECTURE.md)
- [`EXECUTION_AUTHORITY.md`](EXECUTION_AUTHORITY.md)
- [`FULL_AUTO_RECONCILIATION_20260730.md`](FULL_AUTO_RECONCILIATION_20260730.md)
- [`fleet-recovery-rollout.md`](fleet-recovery-rollout.md)
- [`self-improvement-cycle.md`](self-improvement-cycle.md)
- [`kv-cache-control-plane.md`](kv-cache-control-plane.md)
