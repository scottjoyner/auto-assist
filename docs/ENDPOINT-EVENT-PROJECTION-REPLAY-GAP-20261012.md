# Event projection retry gap — isolated proof and release gate

**Draft-only negative experiment. No new production route, replay endpoint, cursor,
repair operation, graph write or migration is authorized.** Stacked on draft
#337 (legacy writer fence) and draft #336 (identity collision disambiguation).

The currently deployed `swarm_core.record_event` stores a canonical
`EventEnvelope` in a separate Neo4j session and then calls
`reconcile_event()`. On identical retry it returns early from the existing
event/idempotency-key branch with `deduped=True, graph_reconciled=False`,
without checking whether the first projection succeeded or calling the
reconciler again. The ledger records an input receipt but does not provide
an atomic applied-projection receipt.

## Physical negative scenario

In an internal-only disposable Neo4j 5.26 Enterprise graph (no published
ports, mounts or production credentials) use the exact `record_event`
implementation with fabricated IDs. Intercept only its projection call and
force a synthetic crash after the ledger commit. Verify an EventEnvelope
exists, the intended SwarmNode does **not**, and the same event's retry is
deduplicated without reconciliation. Reinspect the graph after the retry to
confirm the projection remains absent. No synthetic event is sent to any
live app. The test container and network are removed unconditionally.

A separate in-memory fake session exercises that precise source path in
hosted CI without Neo4j. The unwired `event_projection_replay_shadow.py`
qualifier refuses all automatic replays, including when a projection receipt
or fence attestation is missing or has an uncertain transaction outcome.
Even a perfect fabricated atomic-applied receipt only confirms *prior*
projection; it never authorizes a retry or change to graph data.

## Required owner-led design (NOT implemented)

- Decide if canonical event storage and graph projection must commit in
  **one Neo4j transaction**. The current projection helpers open their own
  sessions, so blindly wrapping `record_event` in a transaction is not enough.
- Alternatively design an explicit, immutable projection outbox with
  transactionally claimed jobs, a per-event source-digest-bound
  `applied` receipt, monotonic fencing term and crash recovery.
- A receipt written *after* the projection without the same atomic commit
  may be missing even when effects applied; this is an uncertain-effect
  condition, not safe replay authorization.
- Preserve per-event identity/ownership, privacy and source provenance.
  Reject conflicting digests and concurrent consumers, never re-apply a
  stale service registration after an endpoint ID remapping.
- Run restart, duplicate, split-brain, stale-term, partial-commit and
  cross-producer tests, with graph-owner snapshot/rollback and independent
  negative replay witnesses. No new production replay path until issue
  #322 and credential custody #156, graph dual-stack isolation, schema and
  gateway controls have been independently accepted.

This source-only experiment **does not prove** any production event is
currently orphaned, nor does it authorize scanning or repairing live
EventEnvelope nodes. Actual orphan inventory requires a separate approved
read-only and privacy-bounded witness.
