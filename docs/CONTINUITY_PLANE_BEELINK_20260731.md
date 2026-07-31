# Beelink continuity plane — July 31, 2026

## Decision

The Beelink recovery node is promoted from a cold restore target into a bounded continuity controller.

FalkorDB and a separate Redis queue provide the hot operational state needed to continue routing, maintain fenced roles, expose current runtime projections, distribute bounded tasks, and retain an idempotent durable-event outbox while Neo4j is unavailable. Neo4j remains the final durable state and audit authority.

FalkorDB is not a replacement primary database. Its contents must be reconstructable from signed observations, runtime reports, task events, backup artifacts, and the Neo4j durable ledger.

## Authority boundary

```text
AssistX policy and signed recovery authority
  -> recovery epoch and exclusive fence
  -> Beelink continuity plane
      -> hot state, leases, projections, bounded tasks, event outbox
      -> strict-offline router to approved LM Studio nodes
  -> separately fenced durable tier
      -> restore Neo4j system + application databases
      -> replay final events into Neo4j
  -> separately promoted worker and Hermes
```

The continuity plane cannot:

- discover or admit inference nodes by itself;
- load or unload models;
- create arbitrary shell or SSH commands;
- approve its own recovery activation;
- rewrite durable task history without a signed outbox event;
- start Neo4j while the Beelink's local model still occupies the reserved RAM envelope;
- promote Hermes before a fenced synthetic task passes.

## Hot state contracts

The standalone `assistx.continuity_api` provides:

- signed, cluster-bound, idempotent continuity events;
- monotonically increasing recovery epochs;
- epoch-bound role leases and fence tokens;
- service heartbeats with capability and memory reports;
- capability-based task submission, polling, claims, and finalization;
- TTL-bound runtime and context projection documents;
- bounded KV/context metadata without raw prompt or token material;
- a durable outbox for later Neo4j commit;
- router-compatible runtime projection, context projection, event sink, and backlog endpoints;
- current memory-envelope and coordinator plans.

Atomic epoch, lease, task, and idempotency behavior uses Redis primitives exposed by FalkorDB. Graph projections are secondary and best-effort: a graph query failure cannot invalidate the atomic ledger.

## Durable event contract

Events use three durability levels:

- `ephemeral`: observation only;
- `recoverable`: retained in bounded hot state and reconstructable;
- `durable`: retained in the outbox until Neo4j acknowledges commit.

The Neo4j continuity reconciler uses event IDs for idempotency. Current specialized commits include:

- final task completion and result digest;
- recovery epoch advancement;
- portable context/KV manifest metadata.

Every durable event is also retained as a `ContinuityEvent` audit node.

## Memory and service gates

The Beelink has approximately 14 GiB usable RAM. The default continuity profile reserves roughly 4.4 GiB for the host and continuity services, approximately 5.5 GiB for the small headless model, and at least 1.8 GiB safety headroom.

The memory planner supports:

```text
standby     continuity services plus small model
continuity  active failover coordination plus small model
 durable    continuity services plus Neo4j; local model drained
executor    promoted worker; Hermes still separately gated
```

The hardened recovery executor checks:

- required prerequisite deployments;
- current `MemAvailable`;
- configured forbidden process tokens;
- signed activation epoch, bundle checksum, and fence proof.

`assistx-durable` requires `assistx-continuity`, at least 4096 MiB available, and no `lmstudio`, `llama-server`, or `lms server` process.

## Primary controller policy

The primary AssistX deployment must configure automatic activation explicitly:

```text
ASSISTX_RECOVERY_ISLAND_AUTO_ACTIVATION_ENABLED=true
ASSISTX_RECOVERY_ISLAND_AUTO_ACTIVATION_DEPLOYMENTS=assistx-continuity
```

Do not include `assistx-durable`, `assistx-shadow`, `assistx-executor`, or Hermes in that automatic allowlist. Those tiers require separate approvals because they consume materially more memory or restore execution authority.

Automatic actions should remain narrowly bounded. A recommended initial policy is:

```text
ASSISTX_RECOVERY_ISLAND_AUTO_APPROVE_ACTIONS=stage,verify,activate
```

The activation still requires the controller's recovery lease and second signing key. Complete-primary-loss activation still requires an independent witness or manual break-glass proof.

## Runtime projection replication

Before a primary failure, AssistX should periodically publish short-lived signed copies of:

- approved runtime identities;
- model instances and compatibility fingerprints;
- LAN/Tailscale access paths;
- capacity and queue limits;
- current context/KV metadata;
- strict-offline routing policy generation.

The Beelink continuity router fails closed when no fresh projection exists. Candidate discovery does not become admission merely because the primary is unavailable.

## Work distribution without SSH

This release uses capability-based pull and claim contracts, not autonomous SSH deployment.

A node heartbeat advertises capabilities, free memory, slot use, and loaded runtime models. The continuity coordinator selects candidates for bounded roles and tasks. Executors poll, claim with an epoch and token, heartbeat the claim, and return an outcome. This lets the Beelink redistribute verification, backup, code, and inference responsibilities without holding SSH credentials.

Hermes auto-SSH and deployment should be a later, independently fenced capability with:

- per-node deploy identities;
- signed immutable deployment manifests;
- one-worktree scope;
- command allowlists;
- post-deployment verification;
- automatic rollback;
- no unrestricted private key mounted into the continuity controller.

## Online Neo4j backup requirements

The recovery artifact set must include current Enterprise online backup chains for:

```text
system
neo4j (or the configured application database)
```

The `system` database contains administrative state not present in the application graph. The Beelink restore entrypoint supports online `.backup` artifacts and offline `.dump` artifacts, checks available `neo4j-admin` syntax, optionally inspects the backup path, restores each required database, and records a digest of the full artifact set.

A database backup is not sufficient recovery evidence by itself. The rehearsal must also prove:

- image and configuration availability;
- router projection freshness;
- hot-state restart recovery;
- durable outbox replay;
- LM Studio node reachability;
- task redistribution;
- process and memory admission gates;
- executor and Hermes promotion order;
- rollback and split-brain prevention.

## Preferred activation sequence

```text
1. stage/activate assistx-continuity
2. acquire continuity roles under the fresh epoch
3. publish runtime/context projections and resume strict-offline routing
4. distribute bounded tasks to healthy fleet nodes
5. drain the Beelink local model
6. stage/activate assistx-durable
7. restore system + application Neo4j databases
8. replay the durable outbox and verify convergence
9. activate compatibility shadow only if full API coverage is required
10. activate assistx-executor
11. run one fenced synthetic task
12. enable Hermes
```

## Merge strategy

The implementation branch is:

```text
resilience-continuity-20260731
```

It was created from the exact green July 30 reconciliation checkpoint. Keep it as a stacked draft until the other agent's changes arrive. Reconcile that work by contract area rather than taking either branch wholesale:

- recovery authority and signing;
- continuity state and durable outbox;
- runtime projection and router contract;
- Beelink Compose and memory envelopes;
- Neo4j backup/restore;
- executor containment;
- documentation and operator ledger.

No production service should be pointed at the new continuity endpoints until both stacked PRs pass and the physical Beelink rehearsal evidence is recorded.
