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
- Two **indistinguishable ACK-loss observations**: one transaction is
  rolled back before COMMIT; the other commits durably before its ACK is lost.
  Both yield the same uncertain exception, but only the independent readback
  identifies which resource state actually exists. Code rejects automatic
  retries and duplicate logical commits; operator review is necessary.
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


## October 15 continuation — resource-owned transactional seal experiment

The local host `x1-370` does **not** have PostgreSQL server tools
(`postgres`, `initdb`, `pg_ctl`, `psql` absent). Both `unshare -Ur`
and unprivileged `bwrap` failed with host permission restrictions. No
Docker service, OS privilege, network, production database, or existing
credential was changed to bypass these limitations. This means a truly
**independently governed disposable resource was not established**.

The next safe local slice adds a `research_finalization` table to the
disposable resource SQLite file. `finalize_under_resource_lock` uses
`BEGIN IMMEDIATE` to inspect the effect and matching receipt and to insert
a one-use finalization row before COMMIT, **within the same transaction**.
A second SQLite writer attempts a concurrent UPDATE during the held lock
and gets `database is locked`. An invalid/mismatched receipt, stale
generation/checksum or duplicate seal fails closed. Terminating finalization
before COMMIT leaves no seal. If the finalization COMMIT happens but its
ACK is lost, a separate readback can see the seal; automatically repeating
the finalization is denied. It is still only
`RESEARCH_ATOMIC_SQLITE_SEAL_ONLY`, not release authorization.

**Remaining, positively reproduced security failures:**

- Once the transaction releases its SQLite write lock, privileged direct SQL
  can mutate the resource *and even the stored finalization row*. The seal
  does not create durable independent effect ownership.
- A privileged SQL actor can fabricate the effect **and** its matching
  receipt *before* finalization; the correctly implemented atomic seal then
  accepts the fictional resource state. Local transactional atomicity
  cannot establish independent source authority or signer custody.
- Restoring the pre-seal SQLite file erases the seal and reopens a repeated
  finalization even while the resource receipt remains valid. Restore-resistant
  external high-water is still missing.
- The inability to isolate a separate PostgreSQL principal on this host
  means privileged-SQL denial at the actual resource authority remains
  untested. Do not equate `query_only`, separate connection, SQLite
  `BEGIN IMMEDIATE`, or Docker availability with access control.
- A committed local seal can be rolled back or bypassed by a different
  privileged process after the COMMIT; no real resource-owned execution
  transaction or ongoing write fence was established.

The new tests remain entirely under pytest `tmp_path`. Their purpose is
to **falsify** premature admission, not justify merging/deploying.
