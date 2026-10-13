# P0: independently sealed graph readback and offline monotonic recovery

**October 13, 2026.** Draft-only Fleet Commander research on x1-370.
Stacked on draft #346 → #344 → #305. Never mounted on production.
No production Neo4j queries/writes, credentials, routing or service changes.

## Objective

PR #346's Ed25519 source+reviewer reservation remains
`PENDING_UNCERTAIN_GRAPH_OUTCOME` until independently observed receipt and
canonical state have been attested. A caller's unsigned "it committed" claim
must not release a reservation. This slice tests *synthetic-only* signed
readback reconciliation with a **third separately generated witness key**.

## Implemented tests

- `tests/runtime_projection_signed_graph_readback_research.py`
  validates the PR #305 two-signer source/approval *again*, separately pinned
  observer Ed25519 signature, exact audience/role/manifest/source digest,
  canonical generation/status, previous generation, exactly one canonical
  and matching approval receipt, fixed bounded observation/expiry and the
  30–900-second canonical lease interval with modest timestamp skew.
- On a valid readback, and only in a research disposable 0600 SQLite ledger,
  use one `BEGIN IMMEDIATE` transaction with exact pending approval,
  generation, source-digest and prior high-water CAS to mark the replay
  tombstone `CONFIRMED_SYNTHETIC_READBACK` and advance the synthetic
  high-water. No graph credentials, writes, routes, model changes or release.
- `tests/test_runtime_projection_signed_graph_readback_research.py`
  checks tampered signatures, forged observer keys, mismatched manifest,
  receipt source digest, expired/future witness, wrong generation/status,
  duplicate canonical/receipt counts, impossible lease, replay, missing
  witness and eight simultaneous acknowledgment attempts.
- `tests/probe_runtime_projection_signed_readback_bolt_physical.py`
  opt-in disposable Neo4j 5.26 Enterprise **internal-only, no ports, no
  host mounts**, random ephemeral passwords, anonymous volumes, cleanup.
  The fixture's independent MATCH-only graph observer reads synthetic
  canonical+receipt rows and signs a readback. An intentionally wrong
  receipt digest is denied before any ledger high-water update; a corrected
  synthetic readback advances it exactly once. A duplicate witness cannot
  advance high-water again. The graph observer is denied SET writes.

**Initial acceptance:** 74/74 offline tests PASS, Ruff clean;
disposable Neo4j **PASS exit 0 in 23.89 seconds** on x1-370.
Exact remote head validation and hosted CI are separate gates.

## Security and availability caveats

1. A signed readback demonstrates what the claimed independent observer
   recorded. It is **not** a tamper-proof source-of-truth if the observer
   private key is compromised, issuance is ungoverned or Neo4j admin users
   can forge/tamper with database rows. Trusted signer custody, resource
   exclusivity and audit journal are unproven in production.
2. A SQLite test ledger is host-local, with no independently sealed
   genesis/cross-host quorum, rollback-resistant storage or distributed
   effect transaction. The test does not allow automatic reconciliation
   after arbitrary production exceptions or failover.
3. The signed snapshot is point-in-time. A privileged graph actor can
   change state *after* sealing; ongoing generation fencing and denial
   of direct admin Bolt paths are still required.
4. The current running AssistX API/worker/Hermes clients use the
   Neo4j builtin admin account on the same graph; a separate Phonelog
   Neo4j account was found on a different configured graph. NO live
   credential, role or graph-network change authorized here.
5. Production canonical-name and approval-ID uniqueness constraints
   remain absent; no migration/backfill/retention or role grant changes.
   Broad parent repository CI has known failures despite focused green CI.

**Disposition:** DRAFT ONLY — offline synthetic reconstruction, not
proof of production graph commit or trusted physical witness provenance.
NO runtime admission, release, unattended 24h renewal, Neo4j cutover,
model autoload, B06 transaction authority or automatic failover.
