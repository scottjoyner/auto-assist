# Disposable asymmetric receipt consumption — evidence and unresolved security boundaries

**October 15, 2026 | Fleet Commander x1-370 | RESEARCH ONLY / NO-GO**

## Goal and limits

The new import-only SQLite research module connects draft Auto-Assist #384's
public-key Ed25519 receipt verifier with a disposable, transactional
challenge-consumption ledger. This tests local atomicity and negative cases,
not independently governed resource, attestor, key, or high-water custody.
It adds no operational CLI, production API, network calls, credential reads,
TPM actions, deployment hooks, or service changes.

The current input trust assumptions are deliberately unsatisfied in this
lab: one operating-system operator controls both SQLite files, verifier
module, public-key configuration, source readback and ephemeral synthetic
signer used by the tests.

## Positive research checks

- Each experiment initializes a fresh SQLite anchor under pytest tmp_path.
  Existing databases are refused by the bootstrap path. The consumption
  entrypoint opens an already-existing anchor with SQLite mode=rw; it cannot
  silently create an anchor when the expected file is missing.
- A BEGIN IMMEDIATE transaction checks the currently held term, sequence,
  nonce, test key epoch and expected prior checkpoint.
- The Ed25519 verifier checks the exact resource/effect identity, adjacent
  sequence, signed challenge, nonce, clock window, public-key root and
  two successive committed-resource readbacks.
- In the same SQLite COMMIT, the anchor updates high-water and inserts
  separate uniqueness-constrained spent-challenge and receipt-digest records.
- Two concurrent consumers with the same old checkpoint yield exactly
  ONE local winner and ONE stale-CAS rejection.
- A normal second consumption is rejected; an ACK lost after a real COMMIT
  is explicitly uncertain and the next attempt sees a stale anchor.
- Missing anchor, unavailable readback, altered signed effect, wrong key
  or signature, wrong challenge, term, generation, and key epoch fail closed.
- Explicit simulated SQLite COMMIT acknowledgment faults before and after
  a durable commit yield the SAME uncertainty exception: neither may be
  interpreted as authorization for automatic retry.

The only positive return marker is:
    RESEARCH_DISPOSABLE_ANCHOR_CAS_ONLY

## Material NO-GO counterexamples (tested as expected adversarial outcomes)

1. Restore a pre-consumption backup of the only anchor database: high-water,
   consumed receipts, and challenges revert together and the same signed
   receipt can be consumed again. Local transactions are not anti-rollback.
2. Privileged SQL can directly reset anchor checkpoint plus consumption
   tables, reopening the same receipt; there is no DBA permission boundary.
3. A colluding signer and fake resource readback can sign a fictional
   generation, satisfy double-readback, and advance the disposable anchor.
   The verification callback is NOT a trustworthy resource-owned witness.
4. Another privileged actor can change the disposable source after the
   second readback but before anchor COMMIT. Atomicity of the anchor
   cannot lock the separately administered resource.
5. A valid Ed25519 public-key check and local challenge consumption do not
   prove the purported COMMIT caused any external effect.
6. The test key pin, key epoch, and anchor are mutable by the same host
   administrator; there is no independent non-exportable signing key,
   root custody, monotonic service, owner term/fencing, or key revocation.

The source contains explicit tests producing a positive research marker
despite the deliberate forged callback, snapshot restore or stale-effect
race. Such test PASS results demonstrate the limitations, not a GO signal.

## Reproducibility and exact source

Files introduced on the research branch:
- scripts/research-consume-asymmetric-receipt.py
- tests/test_research_asymmetric_receipt_consumption.py
- docs/DISPOSABLE_ASYMMETRIC_RECEIPT_ATOMIC_CONSUMPTION_20261015.md

Run the CPU-local targeted suite:

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider tests/test_research_asymmetric_receipt_consumption.py

A wider selected source-only suite covers earlier #375/#377/#378/#380/
#382/#383/#384 contracts as well as the legacy cutover fixtures.
The combined source verification will be recorded against the exact
commit and local synthetic merge SHA, without inferring approval.

## Remaining next approval and authority requirements

Only independently approved disposable infrastructure under independently
administered host and key custody could test a genuine resource-owned
COMMIT witness and a restore-resistant external monotonic authority.
Require a non-exportable source signing key whose authorization is tied
to real committed effects (not mutable DBA readback), externally protected
pinned verifier roots, durable replay/high-water guards across backups,
revocation and term/fencing, privileged SQL and collusion denials, plus a
provably fenced effect-time finalization across the resource and anchor.
Treat every uncertain COMMIT as requiring independent terminal readback.
Never automatically retry based on an absent ACK.

All outstanding security HOLDs continue: Auto-Assist #156/#299,
Kipnerter API cryptographic #46 / production graph #31 / hosted CI #45,
Auto-Router owner receipts, and real iOS/TestFlight acceptance.
**DRAFT. NO MERGE, DEPLOY, PRODUCTION GRAPH, CREDENTIAL, NETWORK,
MODEL, FAILOVER, OR IOS/APPLE ACTIONS.**
