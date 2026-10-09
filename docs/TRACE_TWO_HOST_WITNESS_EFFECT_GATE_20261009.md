# #148 research — independent witness and synthetic effect fencing

**Date:** 2026-10-09 EDT. **Status:** experimental; NO-GO for production, HA, or real failover.

## Hypothesis and failure boundary

The prior physical tests reproduced two valid-looking copied SQLite authorities
each issuing sequence-1 grants despite intended global capacity 1. Merely
pinning an epoch, checking a file inode, and keeping a caller-supplied
minimum sequence does not prevent copied-state split brain.

This follow-on adds **one** disposable independent witness on xwing.
It is a single point of serialization, NOT a quorum or distributed failover.
Both claimant origins may submit requests over preexisting SSH, while all
simulated-effect state is written atomically inside the witness SQLite DB.

The local copied SQLite grants remain NONAUTHORITATIVE. The witness must
admit a new effect separately; a grant from a local copy is not permission.

## Witness state and invariants

- Head: logical graph, immutable epoch, current holder, increasing term,
  durable sequence and schema version; capacity is fixed at **1**.
- Grants: unique request reference and token, originating holder, term,
  sequence and active/done state. Retries with the same request ID deny.
- Synthetic effects: unique effect reference committed in the SAME SQLite
  transaction as the active-grant/current-term checks. No real workload.
- A new reservation requires current holder + exact term, unoccupied slot.
- Rotation to a *different* holder requires expected term and zero active
  reservations; otherwise `reconciliation-required`.
- Former term cannot create a new grant, complete another grant, or write a
  synthetic effect. Release/replay cannot free a current-term reservation.
- A missing, corrupt, identity-mismatched, or rolled-back witness refuses.
  Its recovery requires separate operator reasoning; it must not bootstrap
  automatically or recover by restoring a copied witness snapshot.

## Physical observation — x1-370 and xwing

- xwing hosts the disposable witness, with **no added listener or port**.
- x1-370 -> xwing SSH: admit `synthetic-x1-over-ssh` -> admitted, term 1,
  sequence 1, active 1.
- Competing request through the same witness -> `capacity-full`.
- Attempted rotation while active -> `reconciliation-required`.
- Synthetic effect against current grant -> `applied-synthetic`.
- After completing the grant, rotation x1-370 -> xwing succeeded:
  term 2 and active 0.
- Old term-1 synthetic effect -> `stale-effect`, no application.
- New xwing term-2 request -> admitted, sequence 2. Read-only observe
  confirmed holder xwing, term 2, active 1, capacity 1.
- Forced SSH ProxyCommand failure -> exit 255 (controlled transport
  failure, **not** a real network partition).
- Missing-witness-path CLI -> `witness-unavailable`, exit 4.
- All disposable witness and test-overlay paths on xwing were removed
  and verified absent. No persistent service or runtime configuration.

## Focused tests

- Both hosts exercise concurrency and copied-state negatives against the
  same research module using unit fixtures.
- Independent witness copy **also** admits both sides. This is the
  intentionally reproduced second-order split-brain counterexample.
- External monotonic term watermark detects same-inode witness rollback;
  a stale floor does not. Current watermark is not independently signed.
- A deliberately spoofed holder can supply the latest term and gain a
  research reservation. **Caller identity is not authenticated** by this
  fixture, even when SSH protects transport between known machines.

## Still blocking real authority

1. Authenticated and independently attested owner/issuer identity, rather
   than the current user-provided `holder` field. Existing SSH user access
   is not application-level claim attestation or signer custody.
2. A single independently controlled durable monotonically increasing
   fencing authority whose recovery does not replay/copy stale history;
   real quorum/election if tolerance of witness loss is desired.
3. Real Neo4j/Redis/execution adapters must atomically validate current
   fencing term at their OWN commit boundary. Checking first and then
   calling an unfenced adapter is unsafe (TOCTOU).
4. Crash, suspended process, copy/restore, unavailable witness, release
   replay and uncertain side-effect reconciliation across physical nodes.
5. Exact-head tests, independent security review, production trust-header
   and ingress review, and budgeted staging performance acceptance.

**No production runtime, provider calls, Neo4j, Redis, NAS, graph records,
deployment, model routing, or new exposed port was changed.** This PR must
remain draft until independent gates accept a concrete adapter and topology.
