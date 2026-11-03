# Atomic review decision + inert child + held outbox — research NO-GO

**Draft research only. Not routed or enabled, not signed, not dispatchable.**

## Source defect and side-effect ordering

The existing /api/review/tasks/{task_id}/approve route checks a Task status
and calls create_task_with_context. That call may create a READY Task, send
an auto-assignment notification, create a context packet, and when requested
invoke Paperclip dispatch BEFORE the review ticket is marked DONE with
review_decision='approved' in a separate Neo4j write. If the second write
fails, the candidate and external side effect can remain without a finalized
audit approval. Multiple concurrent approval calls may independently create
candidate Tasks. These are verified source-ordering exposures; no live
production incident or forged operator request is alleged.

## Research prototype: no external work or implicit admission

review_atomic_approval.propose_held_review_approval is a SOURCE-ONLY helper
that no API, CLI, background job or operator button calls. The caller passes
a source review ID, claimed operator name, exact source updated_at_ts revision,
bounded note and auto_dispatch=False. Inputs are validated before any graph
session. It deterministically derives one candidate ID and one outbox ID from
the review ID, without trusting caller-generated Task identity.

One Neo4j write transaction:
1. Takes the shared canonical WorkSupplyState write lock, requiring EXACTLY
   one row or rolling back.
2. Re-reads exactly one intent_review Task AFTER acquiring the lock. It must
   be REVIEW with the exact revision, no existing review decision/actor, claim,
   session, lease, machine authorization, epoch/generation/approval, Hermes
   scope, target, auto-dispatch, previous APPROVED_AS relationship, Task with
   matching approval origin, candidate ID or outbox ID.
3. Parses the review's own bounded source payload to derive the candidate
   title. Unreadable/untrusted payloads abort the same transaction.
4. Atomically creates one child Task with status PENDING_ADMISSION, an outbox
   record with status HELD_UNTIL_ADMISSION, all provenance relations, and
   marks the source review DONE with decision approved_held. A successful
   review approval does NOT produce Task status READY, a dispatch event, a
   signed approval, auto-assignment notification, context ingestion or
   any Paperclip call. The held outbox has no consumer/dispatcher in this PR.

The deterministic IDs and shared lock protect only collaborating writers.
The unique review/task/outbox identities are not proven against arbitrary
direct-Bolt writers or database rollback; physical database constraints and
permissions remain a P0 design gate. READY promotion and actual external
side-effect dispatch require separate owner-approved protocols.

## Local physical validation, November 3, 2026, x1-370

- 29/29 isolated source tests PASS for shared lock, one post-lock review
  read, inert child/held outbox, deterministic IDs, invalid JSON/inputs,
  missing/duplicate coordination and explicit absence of a wired caller.
- 32/32 disposable real Neo4j tests PASS: creates all linked graph records
  atomically with no Dispatch node, duplicate concurrent approvals have one
  winner, stale revision, protected Task/claims, existing identifiers, missing
  or duplicate canonical lock all fail closed; a synthetic exception thrown
  AFTER all CREATE operations rolls back the entire transaction and serial.
- Broader combined source and graph regression suites are run separately
  and exact-head hosted CI must be confirmed independently before claiming
  any hosted PASS.

All tests use synthetic Task data in an isolated resource-limited Neo4j test
container with Bolt bound only to loopback. No production credentials,
Tasks, models, agents, services, live dispatch, approval or signed receipts.

## Separate release blockers

- Operator name is supplied by the caller and NOT independently verified.
  Neither operator roles nor signed owner approval are established.
- No signed current work admission: live WorkSupplyState remains zero/free
  slots 0/0, background disabled, FleetProjectionState expired. Held candidate
  must NOT be promoted automatically or dispatched.
- The existing review routes (approve, reject, clarify), review approval
  create-with-context and operator proposal endpoints still use their old
  potentially racing semantics. This module does not change them.
- No source-authoritative global claim, cancellation/revocation, machine
  principal replay protection or independent non-rollback custody.
- Real external Paperclip delivery requires an effect-outbox protocol that
  distinguishes prepared, admitted, delivered and independently accepted
  events, with signed receipts and idempotent external reconciliation.
- Physical graph writer privileges and direct Bolt callers must be scoped.
  Dynamic query/callback writer census remains HOLD with 47 unresolved sites
  in this research checkout.

**Decision: DRAFT / HOLD / PRODUCTION NO-GO. Do not merge, route or deploy.**
