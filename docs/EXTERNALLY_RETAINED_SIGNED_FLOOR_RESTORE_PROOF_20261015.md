# External signed high-water floor: conditional rollback detection

**2026-10-15, x1-370. DRAFT RESEARCH ONLY. SECURITY NO-GO.**

## Question and exact conditional answer

A consumed receipt can look like a first-time pending anchor advancement when
the entire anchor has been restored. Auto-Assist #390 shows that no combination
of local resource/anchor reads can distinguish these two histories.

A genuinely externally retained, freshly signed, strictly current high-water
floor **can detect** a local anchor behind that recorded floor. A signature
**cannot establish** that the supplied floor is the most recent one, nor
independent signing/root/restore custody. This experiment assumes the required
separation; it does not implement it.

## Method and observations

The import-only verifier scripts/research-external-signed-highwater-floor.py
accepts an independently supplied public Ed25519 root, pinned key epoch, fresh
challenge and clock, structured signed resource/term/seq/nonce/receipt-digest
floor, and the expected effect. It checks the domain-separated signature and
bounded freshness window. It uses the SAME pair of stable local snapshots
for terminal classification and external-floor comparison. No cross-database
atomic read, mutation, signer, or operational release interface is added.

The tests generate ephemeral synthetic Ed25519 keys only in pytest memory.
They exercise actual SIGKILL subprocess crash fixtures from #389:

- Signed floor seq11, local anchor seq11: SOURCE_NOT_OBSERVED_HOLD or
  SOURCE_COMMITTED_ANCHOR_PENDING_HOLD.
- Signed floor seq11, local consumed anchor seq12:
  EXTERNAL_WITNESS_BEHIND_CURRENT_ANCHOR_HOLD.
- Signed floor seq12 and matching consumed anchor seq12:
  ANCHOR_CONSUMED_OBSERVED_HOLD (never release GO).
- **Material rollback detection**: retain signed floor seq12, restore anchor
  backup seq11. Previous local #390 classifier reports apparently legitimate
  pending anchor; the external floor detects it and reports
  EXTERNAL_WITNESS_DETECTS_ANCHOR_RESTORE_NO_GO, without writes or retry.
- Forged public-key signature (without root substitution), expired/stale
  key epoch or challenge, malformed witness, nonce/digest conflict,
  untrustworthy local ledger or changes between two readbacks: DENIED.

All outputs unconditionally set release_authorized=false and
retry_authorized=false.

## CRITICAL positive counterexamples that retain NO-GO

1. **External tip can itself be replayed/rewound**: restore the anchor
   seq12-to-11, also provide a previously signed valid seq11 witness with
   the old signer key and a caller-matching challenge. The result again
   looks like ordinary source-COMMITTED/anchor-PENDING history. The
   verifier CANNOT determine whether a newer floor existed.
2. **Root substitution**: a shared administrator who changes the verifier's
   pinned public key can produce a fresh signed seq11 claim after restore.
   The signature verifies; the same ambiguity remains.
3. **Shared signer and DB administrator**: coherent source/anchor rows can
   still be fabricated, and a signer can sign fictional COMMIT data.
   The floor alone is not an independently governed effect witness.
4. **No external authority was exercised**: test operator owns synthetic
   signing keys, root pin, local SQLite files, expectation, challenge,
   and clock. Earlier #378 proved multiple existing fleet nodes still
   share one operator's SSH/Docker control. Merely running this code on
   different containers/hosts would not establish independent custody.
5. The two SQL reads are not an atomic multi-DB snapshot; mutable state
   may change after verification. No real effect-time or term fencing.
   Device power loss and external snapshot restore are untested.

Positive adversarial-test PASS is evidence of an existing NO-GO, not security
acceptance.

## Reproduce (CPU-local, no production resources)

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider tests/test_research_external_signed_highwater_floor.py

Only disposable pytest tmp_path SQLite and synthetic Ed25519 keys are used.
SIGKILL fixtures kill their own opt-in child PID. No real TPM, graph,
network, database, key rotation, model, iOS or service change.

## Next authority gate

An independently operated monotonic witness must prove latest durable tip
supremacy across backup/restore and revocation. Non-exportable signer custody,
protected verifier root pin, and resource-owned COMMIT attestation must be
independent of source/anchor DBA and shared fleet host administrators. Deny
privileged anchor AND witness restore, root replacement, signer/DBA
collusion, effect-time mutation and cross-system uncertainty before any
release or automatic retry.

All Auto-Assist #156/#299; Kipnerter API #46/#31/#45; Router owner
receipts; iOS device and release HOLDs preserved. DRAFT / NO MERGE,
NO DEPLOY, NO CREDENTIAL, GRAPH, NETWORK, TPM, MODEL, FAILOVER OR IOS ACTION.
