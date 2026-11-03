# Research-only legacy Intent cancellation / machine-claim fence

Decision: **HOLD / no production merge, cutover, dispatch or agent token issuance**.

## Scope

This branch changes only the two legacy Intent-to-Task cancellation callers,
api._cancel_tasks_for_intent and intent_orchestrator._handle_cancel, to call
legacy_intent_cancel.cancel_legacy_intent_tasks. It does not enroll other
Task status/revocation writers or grant any machine permission.

The helper starts a real Neo4j write transaction, obtains the write lock on the
canonical WorkSupplyState node, then **in a subsequent Cypher statement**
re-reads each candidate Task's current status and protected machine/Hermes
provenance. The protected Tasks are excluded. If the canonical coordination
node is missing or nonunique, the operation fails closed with a typed error and
the transaction rolls back. It does not regard missing Task approval evidence
as authorization for machine execution.

The helper does **not** supply a signed cancellation approval, independent
commit witness, external effect receipt or exhaustive writer inventory.
Legitimate cancellation of a scoped-machine Task needs its own machine
owner-authorized, generation-bound protocol.

## Verified research cases

- 11/11 focused source-contract tests, including missing/duplicate lock and protected
  Task classifications.
- 4/4 real disposable Neo4j tests for protected/ordinary Task separation,
  missing/duplicate canonical lock rollback and partial machine approval denial.
- One concurrent claim/cancel regression in the scoped-claim disposable graph
  suite with a successful scoped claim and zero protected cancellations.
  Full combined disposable graph suite: **51/51 passed**.
- Broader relevant synthetic source regression: **136 passed**, one existing
  Starlette/httpx deprecation warning.
- All disposable tests use independently synthetic Task IDs and credentials,
  loopback-only Neo4j Bolt port, uniquely named test container and limits.
  No production Task or service mutation.

## Explicit unresolved release requirements

1. The generic neo4j_client claim, heartbeat and completion methods retain
   optional claim IDs, zero-attempt and terminal-rewrite bypasses.
2. Stale-claim reapers, terminal history retention, execution preemption,
   migration, direct API mutations and arbitrary external Bolt clients still
   operate outside this shared lock or lack proof they cannot alter protected
   Tasks.
3. Machine-token rotation, independent nonrollbackable approval/revocation
   custody, source-authoritative writer census and resource-side acceptance
   witnesses remain unresolved.
4. Canonical production slots remain zero/expired and five deployed legacy
   agents still have HTTP 401 on authenticated Task listing.

**No production deployment or Task admission authorization is implied by
this patch or its passing tests.**
