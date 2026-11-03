# Research-only maintenance writer fence — HOLD / no deployment

## Scope

The scheduled Task stale-claim reaper, separate API background stale-claim
reaper thread, and scheduled terminal Task retention now use one
legacy_task_maintenance helper. For every Task-writing invocation the helper:

1. Starts an explicit Neo4j write transaction.
2. Obtains a write lock on the canonical WorkSupplyState node via a counter
   increment, requiring **exactly one** matching row. Zero/duplicate rows
   produce a typed exception and transaction rollback.
3. Re-reads current Task eligibility and provenance in a **separate query
   after acquiring the lock** (not a pre-lock snapshot).
4. Refuses stale-claim reset and terminal Task deletion for Tasks with any
   scoped-machine authorization, projection/epoch/approval evidence, sticky
   Hermes scope, Hermes owner, or explicit node target.

Ordinary unprotected stale Tasks and eligible old unprotected terminal Tasks
retain the prior behavior, while failure to obtain the lock blocks only
Task maintenance; the existing independent MemoryItem cleanup and search-index
rebuild still run in the scheduled job. This fallback behavior needs
explicit operational review before any deployment.

## Physical validation (x1-370, November 3, 2026)

- 23/23 focused synthetic source tests PASS, including entrypoint wiring,
  exact-one lock, missing/duplicate lock, malformed bounds, and scheduled
  maintenance fallback behavior.
- 57/57 real disposable Neo4j graph integration tests PASS, covering
  protected machine/Hermes/targeted Task refusal, stale reaping of ordinary
  legacy Task, terminal history retention, malformed lock rollback, real
  two-transaction lock contention, and concurrent valid scoped claim vs
  maintenance reaper helper with expired synthetic lease.
- 159/159 relevant combined source tests PASS, one existing Starlette/httpx
  deprecation warning. Ruff and Python compile PASS.

All Neo4j tests used an independent resource-limited container with Bolt
bound only to 127.0.0.1, synthetic graph nodes and no live Task writes.

## Unresolved / production NO-GO

- These are **three** covered maintenance Task mutation paths only.
  Generic claim/heartbeat/completion, idempotent result rewrites, preemption,
  migration, cancellation endpoints beyond the two Intent paths, passive
  claims, direct Bolt clients and all external effects remain unproven.
- The independently running MemoryItem garbage collector is **not**
  transaction-fenced; it may delete protected Task-linked MemoryItem evidence
  even while the Task node is retained. Do not call this a complete retention
  custody guarantee. The answer index rebuild is also outside this fence.
- Claims and reapers serialize only if they participate in this canonical
  coordination lock. Nonparticipating writes and privileged graph rollback
  remain physically possible, as established by prior P0 #427 testing.
- The helper deliberately skips all targeted Tasks, even ordinary ones,
  potentially increasing retained history. Review resource growth and
  owner classification before considering rollout.
- Scoped claim/execution research modules are not active production endpoints.
  Machine credentials and session replay are not owner-approved; five legacy
  agents still receive authenticated Task 401, production projection is
  expired and there are zero approved slots. Independent custodian and
  source-authoritative generation fencing are not established.

**Decision: HOLD, production NO-GO. No production merge, service changes,
credential changes, live Task cleanup, model activation, or routing cutover.**
