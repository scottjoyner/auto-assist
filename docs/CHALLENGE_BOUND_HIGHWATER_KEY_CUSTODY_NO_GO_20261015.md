# Signed high-water challenge: freshness is not administrator independence

**October 15, 2026 | Fleet Commander x1-370 | RESEARCH-ONLY | NO-GO.**

## Current physical boundary

Fleet Commander established on x1-370 that the same operator can use
read-only SSH and reach Docker on xwing and destroyer. That is **not**
independent governance of a resource database versus replay high-water,
even when the databases run on different machines.

The current hardware capability check found TPM device nodes on x1-370,
xwing and destroyer, but **no tpm2 tools** on any of these nodes.
The current x1-370 operator cannot read its tpmrm0 device. We made **no**
TPM requests, generated no hardware keys, reset no counters and changed no
device permissions, drivers, secrets or firmware. TPM presence alone does
not establish owner-separated storage, restore protection, independent root
custody or an appropriate monotonic counter API.

## Smallest safe synthetic cryptographic experiment

The import-only pure verifier in
scripts/verify-challenge-bound-highwater.py uses standard Ed25519
verification in Python cryptography with in-memory generated test keys:

- Strictly domain-separated, canonical high-water statement and exact
  resource identity digest.
- A 256-bit expected challenge supplied **independently** of the signed
  message, with freshness <=60 seconds and an independently supplied clock.
- A caller-pinned Ed25519 public key and **externally pinned key epoch**;
  the statement cannot choose its own untrusted key.
- Resource-bound exact term/commit sequence, revision/checksum/evidence
  digests, nonce and minimum prior high-water checkpoint.
- No network, file, credential, database, Docker, TPM, release CLI or
  readback operations. No state updates and **no automatic retry**.
- No output can be a production GO. Even a passing signature is labeled
  RESEARCH_SIGNED_STATEMENT_ONLY.

The tests provide negatives for forged or substituted signatures, unknown
keys, boolean numeric-schema coercion, replayed challenge, expired/future
statement, old key epoch, changed resource effect, rollback below an
independently supplied minimum checkpoint, nonce conflict, and missing
authority input.

## Critical reproduced NO-GO counterexamples

1. **Compromised trust root:** a common administrator substitutes the
   caller's purportedly pinned public key with their own, signs fictional
   resource evidence, and obtains a valid research consistency result.
   Key pinning cannot be verified if the same attacker controls the
   verification code, its configuration or the trust anchor.
2. **Restored external checkpoint:** a previously accepted statement is
   rejected when a genuinely newer high-water exists, but becomes
   acceptable again when the minimum checkpoint is rolled back. A fresh
   challenge does not solve *anti-rollback* when an attacker can sign with
   the key and roll back state.
3. **Signer/issuer collusion:** if the anchor signer and expected-resource
   provider collaborate, they can sign a fictional resource generation
   with a current challenge. Ed25519 proves possession of a key, **not**
   that PostgreSQL, Neo4j or a Router committed a real effect.
4. **Post-verification TOCTOU:** the resource can change after verification.
   No resource-owned effect-time fence, durable ACK or term ownership
   is established by this verifier.

## Independently governed next gate

Designate a separately administered custodian for the high-water key,
monotonic storage, its backups/restores and attested access policy. Require
a trustworthy published root pin under distinct owner control; demonstrate
that current x1-370/xwing/destroyer operator access cannot rewrite either
the anchor history or its trusted public key. Validate issuer challenge
freshness and resource COMMIT provenance with independently verified
read-only witnesses, using disposable infrastructure and operator approval.
Reject ambiguous/uncertain COMMIT without automatic retry.

If separate custody is unavailable, preserve the NO-GO rather than
treating hardware availability, a signature, or green CI as a release gate.

Other unmerged research: #368–#378, including #377 physical two-cluster
PostgreSQL results, #378 shared-operator negative fleet witness and #373
fictional Ed25519 COMMIT counterexamples. Open security holds:
Auto-Assist #299/#156, Kipnerter API #46/#31/#45, Auto-Router native owner
receipts and physical iOS/TestFlight acceptance.

**No production changes, merges, deployments, credential/key/TPM writes,
network/failover actions, graph effects, model promotions or iOS release.**


## 2026-10-15 continuation: replay ≠ observation ≠ advancement

The **original read-only signed statement verifier** will correctly accept
the same valid envelope multiple times if the caller presents the same
expected challenge and the same minimum checkpoint. It can also verify a
*newly signed* challenge for a checkpoint which is already current. These
are not necessarily flaws in observing an existing state, but they are
unsafe if an operator interprets `RESEARCH_SIGNED_STATEMENT_ONLY` as a
one-time commit receipt, consumed challenge or admission authorization.

A separate `evaluate_strict_advance` helper now denies equal
`(term, commit_seq)` positions and returns the explicitly limited label
`RESEARCH_ADVANCEMENT_UNCONSUMED_ONLY` for a strictly greater signed
position. The additional synthetic tests reproduce that **two clients
given the same old anchor position both pass this pure helper**. There
is no atomic challenge consumption, durable lease, term ownership or
independently governed state behind this helper.

### Actual two-database PostgreSQL 17 contention (disposable only)

The pre-existing explicit-opt-in, networkless, tmpfs-only two-cluster
runner was extended with two additional independent, adverse controls:

1. An authorized anchor writer used `hw.advance` to advance the anchor
   from seq1 to seq4 while the resource was only at seq3. The update
   succeeded; the SQL contract checks monotonicity, **not** whether
   seq4 corresponds to an actual committed resource effect. This
   actively demonstrates **NO_GO: anchor_writer_can_advance_beyond_uncommitted_resource**.
2. Two separately authenticated anchor-writer PostgreSQL connections
   concurrently attempted `hw.advance` from seq4 to seq5, each
   carrying a different nonce and expected old position. A real
   PostgreSQL row-level CAS produced **exactly one COMMIT and one
   rejection**; readback showed seq5. This is evidence of concurrency
   serialization by PostgreSQL, **not** independent high-water custody,
   source provenance or a release permission.

A shell EXIT/INT/TERM trap removed both disposable containers and the
temporary race-results directory. The physical run independently
confirmed no experimental container or test scratch directory remained.
No production process, network or credential was modified.

### Smallest unresolved proof

Design an authorized resource-owned COMMIT receipt whose term, sequence
and effect identity the anchor can verify *independently*, before
advancing by one CAS, with externally controlled trust roots and
durable challenge-consumption state. Negative tests must include
**authorized anchor writer advancing without resource COMMIT**,
concurrent callers sharing an old checkpoint, restored anchor state,
forged receipt under source-superuser authority and effect changes
between witness readback and finalization. The same current Docker
administrator still controls both PostgreSQL clusters. Preserve
NO-GO, no automatic retry and all no-merge/no-deploy restrictions.
