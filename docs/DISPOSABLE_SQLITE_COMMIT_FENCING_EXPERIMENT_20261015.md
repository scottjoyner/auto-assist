# Disposable SQLite transactional COMMIT/readback experiment

**2026-10-15 · x1-370 · RESEARCH-ONLY · NO-GO.**

This workstream extends synthetic Ed25519 witness draft #373 and evidence
verification draft #372 with **real SQLite COMMIT transactions** against
two **disposable temporary database files**. The resource and simulated
external watermark are **not independently governed** just because they
live in separate files. All test paths come from pytest `tmp_path`.
No connections to production Router, Neo4j, PostgreSQL, fleet, NAS, credentials,
network, failover, or iPhones exist in the harness.

## Experiment boundaries

`scripts/probe-disposable-sqlite-commit.py` is **import-only**, with no
production or automated run path. It models:

- Resource-owned CAS: a single SQLite transaction updates the effect row
  and inserts a commit receipt with matching term, sequence, nonce,
  generation, checksum and revision digest.
- A *separate SQLite file* for an operator-controlled high-water anchor.
  The operator must advance this manually and independently, **never**
  automatically in response to a signed claim.
- Readback via a new `mode=ro` SQLite connection, and a separate local
  Python process in one test, after an actual commit.
- An ACK-loss simulation **after** `COMMIT`, where the client sees an
  uncertain result; code rejects automatic retry or duplicate logical
  commit. Operator readback is needed before any further decision.
- Restoring the precommit resource DB with the high-water intact,
  and restoring the external anchor while leaving the resource committed.
- Direct privileged SQL that forges both a resource effect and its receipt
  without calling the controlled commit function.
- TOCTOU mutation **after** two apparently consistent resource readbacks.

The integration tests are deliberately skipped on the source-only branch
until independently tested alongside #373's unmerged signed-witness verifier.
A local, detached synthetic merge executes these tests against actual
temporary SQLite files.

## Falsification matrix and NO-GO results

A genuine atomic effect+receipt pair can be observed via a separate
read-only connection. Stale term/sequence and duplicate nonce commit attempts
are rejected by the guarded API; a lost ACK does *not* authorize a retry.

**But** a privileged SQL writer can fabricate a matching effect+receipt,
and a valid signer can then make the untrusted research verifier say
`RESEARCH_CONSISTENCY_ONLY`. A restored high-water anchor permits replay.
The effect can change after the second witness read, with no resource-owned
finalization barrier. A separate SQLite file or process is not an external
security domain when the same user can mutate both.

These are reproducible, adverse **NO-GO** findings. They are not acceptable
proofs of term ownership, independent key custody, durable production
resource commits, snapshot anti-rollback, or uncertain-commit fencing.

## Only safe next resource proof

Identify the **authoritative real resource** and independent permissions,
its fenced term/commit sequence, its durable receipt source, and its
restore-resistant external high-water. Validate independently governed
read-only resource readback, wrong-token denial, SQL bypass rejection,
and effect-time TOCTOU in a **separately approved disposable instance**,
not against the production graph. An uncertain COMMIT must remain
fail-closed with no automatic retry or deployment promotion.

Existing HOLDs: Knowledge context-broker crypto #46, production graph #31,
hosted exact-head CI #45, Auto-Assist security #299 and custody #156,
Auto-Router native execution provenance, and Kipnerter/Vitrialuminios
physical iPhone/Apple release acceptance. **No merge or deployment.**
