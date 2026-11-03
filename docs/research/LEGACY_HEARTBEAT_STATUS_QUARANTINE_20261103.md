# Research-only heartbeat and untrusted status-write quarantine

**Decision: HOLD; production NO-GO. Both opt-in guards OFF by default.**

## Physical baseline evidence

Before this patch, two disposable Neo4j reproductions showed the prior
Neo4jClient methods could mutate machine-protected synthetic Tasks:

1. heartbeat_task changed a machine-authorized Task from CLAIMED to RUNNING
   using only a legacy claim/session, bypassing scoped-machine approval,
   projection, token epoch, runtime and revocation requirements.
2. update_task_status changed a machine-authorized Task to DONE with no
   actor/claim/session/lease information at all.

These were synthetic source-behavior reproductions, not incidents in the
production database.

## Explicit research modes (both disabled unless selected)

- ASSISTX_ENABLE_STRICT_LEGACY_HEARTBEAT=1
- ASSISTX_ENABLE_STRICT_UNATTRIBUTED_TASK_STATUS=1

The first mode requires a nonempty exact claim, owner, session match (when
present), positive attempt, fresh lease and active Task status. A single
Neo4j write transaction obtains the write lock on exactly one canonical
WorkSupplyState row, then uses a separate post-lock query. Duplicate Task IDs
are denied. A Task cannot be machine-authorized, targeted, have generation,
approval, epoch or projection markers, have Hermes scope/actor, or be
Paperclip-dispatched. Only RUNNING or an unchanged status may be requested;
PAUSING cannot be reopened. Lease extension is bounded to 1-900 seconds,
metadata JSON is bounded and the claimed agent session cannot change.

The second mode **refuses every call** to update_task_status. Its method
has neither actor nor claim/session information. It cannot authenticate a
direct status transition based on task_id and status alone. Its callers need
a new authenticated transactional protocol before any strict cutover.

Old unguarded paths remain the default **for legacy compatibility only**;
they retain the two physically reproduced unsafe behaviors. Green CI does
not mean active production is fixed.

## Fleet Commander validation, x1-370, November 3, 2026

- Original two baseline bypasses reproduced on disposable Neo4j.
- 28/28 focused source tests PASS, including mode defaults, malformed inputs,
  and provenance/transaction contracts.
- 20/20 new disposable Neo4j tests PASS, including successful ordinary
  heartbeat, machine/targeted/Hermes/epoch/approval refusal, missing/duplicate
  coordination row, duplicate Task ID, zero attempt, wrong session/actor/claim,
  expired lease, revocation after physical lock contention, PAUSING retention,
  direct-status quarantine, and default legacy compatibility.
- 98/98 combined disposable Neo4j tests PASS across heartbeat, completion,
  maintenance, intent cancellation, scoped claim/execution, and existing
  recovery lifecycle.
- 225/225 combined source tests PASS; one existing Starlette/httpx deprecation
  warning. Ruff/compile PASS. Full assembled FastAPI authentication-isolation
  test PASS using read-only source with Docker network disabled.
- Disposable databases use synthetic data with 127.0.0.1-only Bolt ports,
  uniquely named resource-limited containers and no production credentials.

## Unresolved P0

- Guards are OFF BY DEFAULT and active legacy writer behavior remains unsafe.
- Direct-status strict quarantine would block existing API/jobs/CLI/pipeline
  callers. No unapproved cutover is safe.
- Even strict legacy heartbeat is NOT machine admission or accepted-result
  authority; it must not be substituted for scoped-machine execution.
- Other direct Task writers/Bolt principals, generic claim, revocation,
  external effects, independent custody and anti-rollback remain unresolved.
- Source writer census still reports 45 unresolved query/transaction sites.
- Production signed admission projection expired with zero available slots;
  deployed legacy worker credentials still have the Task-listing 401 issue.

**Remain DRAFT / HOLD / production NO-GO. No merge, rollout, credentials,
Task graph, services, model, route or signed approval changes.**
