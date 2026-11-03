# Operator REVIEW/PROPOSED to READY source-only CAS — research HOLD

**HOLD / production NO-GO. This module is not called by any running route,
CLI, worker, scheduled job, or dispatcher. It is NOT an authenticated operator
approval endpoint, a signed admit-to-work receipt, or a release authorization.**

## Why separate operator approvals from execution authority

The pre-existing update_task_status(task_id,status) accepts only an identifier
and a desired Task status, without an actor, claim, session, attempt or expected
state. Existing source callers include:
- HTML /tasks/{task_id}/approve, currently REVIEW -> READY without revision CAS.
- API /api/tasks/{task_id}/approve-proposal, currently PROPOSED -> READY with
  actor audit but no shared lock or expected version.
- /api/tasks/{task_id}/cancel, task execution API, background jobs, pipeline
  executor and CLI workflows, each requiring DIFFERENT authority contracts.

A second review API (/api/review/tasks/{task_id}/approve) creates a new Task,
then completes its review ticket in a later write. This needs a separate
atomic approve-and-create/dispatch review and is NOT addressed by this CAS.

It is unsafe to replace these with a generic status writer that accepts a
freeform actor string. Review approval is different from owner-bound claim,
execution result acceptance, cancellation/revocation or external side effects.

## New source-only research implementation

operator_review_transition.propose_operator_review_transition requires
nonempty caller-asserted Task ID and operator ID, an explicit source status
(REVIEW or PROPOSED) and a positive exact updated_at_ts optimistic revision.
In one real Neo4j write transaction it:
1. Acquires a write lock on the canonical WorkSupplyState node, requiring
   EXACTLY one canonical row. Zero/duplicate nodes abort and roll back.
2. In a separate POST-LOCK Cypher query, requires exactly one Task matching the
   ID and expected status/revision, and denies any existing approval,
   claim, session, attempt, lease, target, machine authority, epoch, generation,
   Hermes provenance, paused state, auto-dispatch, dispatch relationship or
   allocation reservation.
3. Excludes intent_review tickets (which create distinct approved Tasks).
4. Promotes eligible synthetic REVIEW/PROPOSED to READY, recording actor,
   source state, timestamp and incremented review_version.

This is NOT routed or enabled. The actor argument is a claim by the caller
and is NOT proof of authentication. The existing deployed HTTP authentication
boundary may trust proxy headers and needs independent ingress verification
before binding a future operator route. Task READY can be consumed by legacy
workers, so any future endpoint MUST also enforce current signed admission
and owner-approved dispatch requirements, not simply expose this helper.

## Fleet Commander validation, x1-370, November 3, 2026

- 22/22 focused source tests PASS: lock ordering, input rejection,
  post-lock Task provenance and explicit absence from running caller modules.
- 29/29 real disposable Neo4j graph tests PASS: valid REVIEW/PROPOSED
  promotion, stale version/status, machine provenance, existing allocations,
  duplicate coordination rows and Task IDs, two concurrent approvals with
  exactly one winner, and an approval waiting on the canonical lock while
  machine-protection markers change.
- 247/247 broader source tests PASS; 127/127 combined disposable-Neo4j tests
  PASS across operator review, legacy heartbeat, completion, maintenance,
  cancellation, scoped claim/execution and existing recovery lifecycle.
- Ruff and compile PASS. Test data is synthetic, in resource-limited isolated
  Neo4j containers with Bolt bound to 127.0.0.1. No production credentials.

## Unresolved release requirements

1. Define verified operator principal and role authority independent of an
   arbitrary caller-supplied name, and fix/verify the upstream trusted-header
   boundary for all ingress paths.
2. Require source-authoritative owner approval and currently valid signed
   fleet-work admission before READY can be used to dispatch. Live canonical
   projection remains expired with 0 approved/free slots.
3. Migrate each current status-mutating caller into the appropriate distinct
   claim, result, operator, cancellation or recovery protocol; do not globally
   enable strict unattributed Task-status quarantine until that work is tested
   and approved.
4. Resolve the source writer census (46 unresolved lexical query/callback sites
   in this research tree) plus independent, non-rollbackable evidence custody,
   physical Bolt-write privilege boundaries, legacy agent Task GET 401 and
   other nonparticipating writers.
5. Establish a separate atomic approval/Task-creation/outbox contract for the
   newer intent_review ticket API before relying on its status/audit record.

**Research module only. NO MERGE, NO PRODUCTION ROUTING, NO TASK DISPATCH,
NO SERVICE/CREDENTIAL/MODEL/GRAPH/APPROVAL MUTATION.**
