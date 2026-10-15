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
