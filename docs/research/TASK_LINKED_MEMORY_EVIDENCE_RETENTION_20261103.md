# Research: Task-linked MemoryItem retention

## Decision

HOLD / production NO-GO. This does not authorize live cleanup, Task
claims, identity migration, deployment, or any external evidence acceptance.

## Reproduced baseline defect

On November 3, 2026 a separate, synthetic disposable Neo4j experiment
reproduced the current independently scheduled MemoryItem garbage collector
deleting a stale MemoryItem with a RELATED_MEMORY relationship from a
machine-authorized Task. The Task remained, but its evidence relationship and
the MemoryItem were destroyed. One physical baseline reproduction passed.
This is NOT a claim of observed production evidence loss.

The production source has long generated these Task -> RELATED_MEMORY ->
MemoryItem links during Task completion. Retaining the Task node alone does
not prove memory/evidence retention.

## Bounded research change

The scheduled maintenance job now calls the same
legacy_task_maintenance canonical writer-lock helper already used by
legacy stale-claim reaping and terminal Task retention. It requires one
canonical WorkSupplyState row, starts an explicit write transaction, then
runs the MemoryItem retention query AFTER the lock. If coordination is
missing/duplicated, it does NOT fall back to uncoordinated deletion.

The query deletes only expired unlinked MemoryItems and refuses deletion
of any record that has a direct Task - RELATED_MEMORY -> MemoryItem edge,
regardless of whether the Task has an intact machine-authority marker.
This conservatively retains generic Task outcome memory too. The index
rebuild may still run independently after a blocked graph cleanup.

## Validation (x1-370, November 3, 2026)

- 33/33 focused synthetic maintenance source tests PASS, including the
  scheduled maintenance caller, malformed retention limits, and missing/
  duplicate canonical lock.
- 60/60 real disposable Neo4j tests PASS across scoped machine claim,
  execution, Intent cancellation, Task maintenance, and memory retention.
  Old unlinked MemoryItems are deleted, while both protected-machine and
  ordinary Task-linked MemoryItems remain attached and intact. Missing/
  duplicate coordination rows retain memory and roll back.
- 169/169 related source tests PASS; Ruff and compile PASS. One preexisting
  Starlette/httpx deprecation warning.
- All physical tests use synthetic graph records on a uniquely named,
  resource-limited Neo4j container, exposing Bolt only on 127.0.0.1.
  No production Task or protected evidence was used.

## Still not authoritative

Direct relationships are only one evidence form: metadata-only, indirect,
external, archive and other link types are NOT covered by this query.
Other MemoryItem writers, generic completion, retention policies, Task
migration, unregistered direct Bolt clients, independent custody and admin
rollback remain outside the shared transaction fence.

Indefinitely retaining all Task-linked outcome memory can grow storage and
needs a custodial retention/export policy with independent receipts,
explicit lifetime decisions and owner approval. A graph lock is NOT
anti-rollback proof or independent authorization.

The underlying machine fleet still lacks owner-approved identities for the
five legacy workers (Task listing 401), and production projection is expired
with zero approved slots.

No production merge, deployment, service restart, credential, Task graph,
model or route mutation is permitted from this research result.
