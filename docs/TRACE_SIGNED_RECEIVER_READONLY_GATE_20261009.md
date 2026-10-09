# #148: pinned synthetic receiver signatures and fencing equality

Date: 2026-10-09. Parent: draft #241. Status: **research only, draft, failover NO-GO**.

## Goal

Distinguish tampered or unpinned receiver testimony from syntactically valid
but untrusted assertions before running the pure uncertain-effect classifier.
This is NOT a production source of truth, durable receipt protocol, or sink
adapter. It adds no keys, listeners, services, provider calls or real effects.

## Contract

- Accept only a separately supplied pre-enrolled Ed25519 public key indexed
  by an exact expected key ID. Never enroll keys found in returned payloads.
- Domain-separate signed receipt bytes as
  `assistx/synthetic/receiver-evidence/v1\0` + canonical JSON bytes.
- Refuse noncanonical bytes, duplicate JSON keys, unrecognized fields,
  malformed encodings/signatures, unknown key ID, unexpected receiver,
  mismatched operation/domain/owner/term/effect identifiers and mixed batches
  with any untrusted receipt.
- A supplied receiver record binds `boundary_term` to the atomic decision.
  Any applied effect with `boundary_term != admitted term` is a safety
  violation. This catches both stale-term and *premature future-term* effects.
- Preserve a legitimate term-1 application that occurred while receiver
  term was 1, even if the witness subsequently advances to term 2.
- No receipt / lost acknowledgment -> `unknown-hold`. Authenticated
  conflicting receipts -> `conflicting-evidence-hold`. All classifications
  set `automatic_replay_allowed=false`, `takeover_allowed=false`.

## What signatures do NOT prove

A valid signature proves only that the corresponding private key signed the
bytes. It does not establish that the key is independently custodied, that
the sender is the physical receiver, that the bytes were fsynced, that the
receiver actually enforced term equality atomically at its real effect sink,
or that the supplied receipt collection is complete. A fixture field
`durable=true` is an assertion, not external evidence.

No external monotonic witness, quorum, TPM/HSM, key escrow, replicated
receipt log, lost-ack durable outbox, real Neo4j/Redis fencing adapter,
network partition test, or production receiver authentication is introduced.

## CI blocker inherited from parents

At #241 exact-head GitHub Actions, the recovery-canary tests reported 11 pass,
one fixture setup error. Full job logs establish an unauthenticated Docker Hub
`toomanyrequests` rate limit when pulling `neo4j:5.26-enterprise`; no
Neo4j lifecycle assertion ran. Preserve RED until an approved authenticated
or digest-pinned image supply is available and the exact-head canary passes.
Never skip the integration test to paint CI green.

## Acceptance still required

1. Exact-head isolated scoped tests on x1-370 and xwing, compilation, cleanup.
2. GitHub exact-head unit, research, recovery-canary; independent reviewer.
3. Physically independent signer-key custody and enrollment, signed durable
   receiver history with its own rollback and equivocation witnesses.
4. Real effect-sink atomic fencing and uncertain in-flight reconciliation
   tested in an approved staging topology. Human approval for any activation.

**Do not merge/deploy for high availability or production failover.**
