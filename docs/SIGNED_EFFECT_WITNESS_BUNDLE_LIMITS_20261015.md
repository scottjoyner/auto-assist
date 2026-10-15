# Cross-signed resource-effect bundle — cryptographic binding is not resource authority

**October 15, 2026 | Fleet Commander x1-370 | RESEARCH ONLY / CRITICAL NO-GO**

## Finding from Auto-Assist #394

The preceding synthetic gate required a real, fsynced effect artifact
before local SQLite witness issuance. However, #393's signed witness
certificate did not include the artifact SHA-256, so its signature
did not cover the additional effect evidence. This draft tests an
**additional signed receipt bundle** binding all three pieces:
effect content, claimed committed resource state, and signed witness
checkpoint. It remains independently verified research only.

## Exact limited protocol

The import-only, public-key-only verifier
`scripts/research-verify-signed-effect-bundle.py` accepts a bundle:

1. A **resource-effect attestation**: domain-separated Ed25519 signature
   from a caller-pinned supposed resource-owner key over a typed claim:
   resource ID, term, commit sequence, generation, checksum, revision
   digest, receipt digest, exact fsynced artifact SHA-256, nonce, fresh
   challenge, key epoch and expiration.
2. A valid, existing **#393 signed witness certificate**: a distinct
   member public key verifies a domain-separated signature over its
   hash-linked high-water head bound to the same term, sequence, nonce
   and receipt digest.
3. A second **witness-signed binding**: exact cryptographic hashes of
   BOTH the resource-effect claim and existing unsigned witness
   certificate, plus the artifact SHA-256, resource/term/sequence,
   nonce, challenge and witness key epoch. This prevents swapping
   one signed certificate for another or replacing the signed effect
   payload without invalidating the binding.

For public-only verification, it strictly checks the domain separation,
schemas, epochs, challenge, clock windows, original signed cert,
effect-attestor signature, witness binding signature, and all expected
effect fields. Then it opens the existing synthetic artifact with
O_NOFOLLOW, checks SHA-256 against an already-open FD and path inode,
calls #390's two-pass read-only source/anchor terminal classifier,
requires *observed consumed* local state and rechecks artifact bytes.
All existing files live under pytest tmp_path. The verifier cannot
commit, sign, retry, repair or authorize real execution.

There is **no new production signing or issuance path**. The resource
and witness private keys are ephemeral test fixtures ONLY.
The join is assembled in the test AFTER #393 member COMMIT and is
therefore **not atomic with member issuance**.

The only positive label is
`RESEARCH_CRYPTOGRAPHIC_EFFECT_BUNDLE_ONLY_HOLD`.
This is *not* production acceptance or a grant to dispatch, deploy,
replay or retry. No operational CLI exists.

## Reproducible observations / critical counterexamples

- Wrong resource ID, generation, term, nonce, receipt digest, epoch,
  expired challenge, wrong artifact hash, missing root, invalid Ed25519
  signature, changed witness certificate, unsigned effect modification
  or misjoined witness-certificate digest are denied.
- File changed or deleted after signing and mismatched source/anchor
  rows are denied on the next verification pass. This is detection of
  observed inconsistency, not protection of a future physical effect.
- **Root substitution**: replacing the pinned effect key and witness
  key lets one administrator issue and verify a new artificial
  cross-signed bundle. Cryptographic consistency passes.
- **Forged authorized COMMIT still passes**: after a real source-only
  COMMIT but no authorized anchor CAS, a privileged database
  administrator fabricates coherent anchor/spent/consumed SQL rows,
  writes the synthetic artifact and controls both signing keys. The
  public-key-only verifier accepts the resulting signed bundle as
  research consistency, even though authorized execution never
  happened. Three signatures from two keys do not prove independent
  resource authority.
- **No cross-resource finalization fence**: a privileged writer can
  alter SQL or file content immediately after a successful verifier
  observation. The earlier accepted signature remains valid, although
  a new verification now rejects the mutated state.
- **Non-atomic signing gap**: the #393 member checkpoint can already
  be consumed while the outer effect-binding bundle is missing,
  as would occur after a crash between issuance and assembly.
  Blind reissue is denied as stale. A durable independent terminal
  reconciliation protocol is still required; DO NOT AUTO-RETRY.
- The expected effect fields, signer roots, challenge, source/anchor
  databases, artifact file and keys all remain under one test process
  and one shared host operator. No independent custodian, TPM, physical
  resource executor, or externally governed monotonic authority was
  created, and nothing was changed on other fleet nodes.

## Reproduce

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider \
      tests/test_research_signed_effect_bundle.py

Tests use actual disposable SQLite resource/anchor/witness COMMITs from
#393/#394, a small fsynced artifact and transient Ed25519 test keys.
No real external database, production graph, credentials, network,
Docker, TPM, model routing, iOS/TestFlight or service changes.

## Required next genuine authority gate

A protected **resource-owned signer** must acquire authorized effect
provenance from a trusted execution boundary not alterable by source/
anchor DBAs or shared fleet OS/SSH operators; signature must be atomic
with, or durably causally linked to, the physical effect and fenced
owner term. The resulting witness/attestation delivery must be
recoverable under uncertain COMMIT without re-execution. Member
private-key custody, trust-root pinning, revocation and external
monotonic history must be separately administered and survive
privileged restore. Independently test signer/DBA collusion, forged
hardware/resource attestation, stale-effects, split-brain, root
substitution and execution after verification.

**Auto-Assist #156/#299, Context crypto #46, production graph #31,
hosted CI #45, Router owner-bound receipts, model admission, and iOS
physical release remain NO-GO. No merge, deploy, credential, graph,
TPM, network, failover, model or iPhone change.**
