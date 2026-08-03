# Degraded Control Plane — 2026-07-31

## Objective

The Beelink recovery node must coordinate the fleet before a full Neo4j restore is available and while retaining enough RAM for a small headless local model. It is not a second permanent primary. It is a bounded degraded controller that preserves safety, delegates compute, and converges final state back into Neo4j.

## State ownership

| State class | Active store | Durability | Authority |
|---|---|---|---|
| heartbeats, runtime health, route observations | FalkorDB | TTL + AOF | reconstructable observation |
| leases, claims, delegation, recovery intents | FalkorDB | TTL + AOF + local journal | temporary operational authority guarded by epoch/fence |
| session and KV-cache manifests | FalkorDB | TTL + AOF | advisory placement/context metadata |
| task outcomes, approvals, audit evidence | Neo4j transaction | durable | final authority |
| physical runtime/model identity | Neo4j projection cached in FalkorDB | bounded lease | Neo4j remains source of truth |
| queue payloads | Redis | AOF, no eviction | transport only |

FalkorDB may never convert an active operation into durable success. `OperationalStateStore.finalize()` commits Neo4j first and only then marks the short-lived operational record complete.

## Recovery tiers

### Tier 0 — degraded coordination

Starts without Neo4j:

- FalkorDB operational graph;
- separate Redis queue transport;
- degraded AssistX API surface;
- strict-offline auto-router;
- recovery, claim, lease, heartbeat, runtime-projection, delegation, and KV-manifest APIs only;
- local append-only reconciliation journal.

Tier 0 may:

- freeze or reject unsafe writes;
- maintain fenced leadership and leases;
- accept node heartbeats;
- route inference to already-approved LM Studio endpoints;
- delegate bounded work to surviving nodes;
- preserve session/KV metadata;
- request and coordinate database restoration.

Tier 0 may not:

- admit new physical runtimes;
- load or unload models;
- mutate repositories;
- start Hermes SSH deployment;
- approve permanent configuration changes;
- claim durable task completion while Neo4j is unavailable.

### Tier 1 — shadow durability

Restores Neo4j and starts the inert AssistX shadow API. Reconciliation compares:

1. Neo4j durable generation and epochs;
2. FalkorDB operational records;
3. local operation journal;
4. current node heartbeats and runtime projection.

Conflicts fail closed. The highest valid fenced epoch wins for temporary coordination; permanent state remains Neo4j-backed.

### Tier 2 — executor promotion

Starts the AssistX worker after Tier 1 convergence. Backlog processing remains rate-limited and memory-budgeted. The Beelink delegates inference and heavy execution to surviving LM Studio/Hermes-capable nodes.

### Tier 3 — Hermes promotion

Hermes remains disabled until one synthetic fenced task proves claims, routing, output evidence, final Neo4j commit, and cleanup. Autonomous SSH deployment is a later capability and must use per-node short-lived credentials, target allowlists, isolated worktrees, and explicit rollback.

## Beelink 14 GB memory budget

Target steady-state ceiling while a small model is loaded:

| Component | Limit |
|---|---:|
| small headless model/runtime | 6.0–8.0 GB |
| FalkorDB | 1.0 GB container / 768 MB dataset |
| Redis queues | 256 MB |
| degraded AssistX API | 768 MB |
| degraded auto-router | 384 MB |
| OS, Tailscale, container runtime, page cache | 2.0–3.0 GB |
| emergency headroom | at least 1.0 GB |

Neo4j shadow restore should not run concurrently with the largest Beelink model. The recovery controller must either unload the local model or delegate all inference before Tier 1 starts. Memory pressure is a recovery signal, not a reason to let the kernel choose victims.

## Load distribution

During degraded operation:

- Beelink owns coordination and low-volume recovery reasoning only.
- Existing LM Studio nodes own inference.
- Nodes with Hermes executors may receive bounded tasks after identity and capability checks.
- Auto-router uses the last signed runtime projection and approved LAN/Tailscale paths.
- No model autoloading occurs from the degraded controller.
- Queue admission uses per-runtime slot limits and global degraded-mode limits.

## Failure policy

- FalkorDB unavailable: freeze new claims; continue health/read-only routing from the last signed projection; restart FalkorDB within resource limits.
- Redis unavailable: reject new work; do not emulate a queue in memory.
- Neo4j unavailable: journal finalization intents, but report them as `PENDING_DURABLE_COMMIT`, never complete.
- Main controller returns: stop new degraded claims, drain active leases, compare epochs, replay journal idempotently, and relinquish leadership.
- Network partition: only the controller holding the newest independently witnessed epoch may coordinate writes. Without a witness, both sides become read-only.
- Memory pressure: shed UI/history, then local model, then optional router caches. Never kill FalkorDB, queue transport, or the recovery agent first.

## Required next implementation slices

1. Wire `OperationalStateStore` into claims, leases, heartbeats, recovery intents, session context, and KV manifests behind feature flags.
2. Add append-only finalization journal and idempotent Neo4j replay.
3. Add degraded API route allowlisting middleware.
4. Publish signed runtime projections into FalkorDB and the local journal.
5. Add Beelink memory-pressure guard and deterministic service shedding.
6. Add primary-return reconciliation and leadership handoff tests.
7. Add node delegation contracts and eventually short-lived SSH deployment credentials.
