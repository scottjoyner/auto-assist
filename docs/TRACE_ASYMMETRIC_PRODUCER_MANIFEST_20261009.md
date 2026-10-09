# Two-authority encrypted trace custody — asymmetric manifest experiment

**Research checkpoint:** 2026-10-09, America/New_York

**Scope:** isolated draft PR, disposable fixture keys and local encrypted archives. No Beelink Samba mutation, NAS writer, receiver daemon, credential deployment, WORM claim or trace-execution authorization.

## Architectural conjecture

The legacy encrypted trace segment bundle has a signed/HMAC protected index,
but the external receiver currently needs the *producer's symmetric
verification/signing secret* to verify its digest. That violates producer
versus receiver key separation: a compromised receiver could forge producer
manifest signatures. Retaining v1 forever also blocks publicly verifiable
cross-host custody.

**Prediction:** A detached, explicitly pinned Ed25519 producer-signed manifest
can attest to the exact **bytes of the pre-existing v1 index**, each
ciphertext digest and plaintext digest, the journal SHA-256, record count,
last record hash, key identity and monotonic producer generation. A receiver
holding only the **producer public key** and its decryption secret should
independently restore the original bytes and validate the entire journal
hash chain, with no legacy HMAC signing key in its API.

After that validation, a *different* Ed25519 receiver signer should issue a
v2 custody receipt cryptographically binding the **full signed producer
manifest digest**. A verifier accepting only pinned public keys and
previously held receipt/producer-generation high-water marks must reject
replay, substitution, wrong node, wrong signing key and tampering.

## Modules and trust boundaries

`src/assistx/trace_producer_manifest.py`

- **Producer-only `sign_verified_legacy_bundle()`:** uses the pre-existing
  legacy HMAC key on the producer to verify a local GPG encrypted v1 bundle;
  checks the content-addressed `<node>/<journal-sha>` namespace and byte
  digests; emits a detached canonical Ed25519 v2 signed manifest.
- **Receiver-only `verify_and_restore_public()`:** requires a pinned
  producer Ed25519 *public* key, producer ID/key ID, independently supplied
  journal and index digests, generation, preceding manifest digest and
  minimum issuance timestamp. Validates v1 index bytes under the detached
  signature, verifies each ciphertext digest, decrypts all segments using a
  separate decryption secret and validates the exact joined journal's row
  chain. **There is no producer HMAC signing-key argument.**
- `manifest_digest()` computes the SHA-256 of the entire manifest including
  signature, allowing an independent anchor to chain subsequent manifests.

`src/assistx/trace_asymmetric_custody.py`

- **Receiver `attest_with_producer_public_key()`:** invokes the public-only
  producer verifier and full restore BEFORE the external receiver signer
  emits an Ed25519 v2 receipt.
- **Verifier `verify_dual_authority_receipt()`:** checks the separate
  receiver key, exact producer ID/key ID and signed producer manifest
  digest, journal/index digests, row count/last row hash, monotonic
  receipt sequence, previous full signed receipt digest and lower time
  bound. The expected receipt predecessor must be obtained from an
  **independently controlled high-water store** and may not be copied
  from the untrusted receipt.

**Key roles:** legacy v1 HMAC stays only with the producer; producer v2
signer private key stays on the producer; detached manifest and producer
public key travel to receiver; GPG decrypt key is separately scoped to
receiver; receiver Ed25519 signer private key stays receiver-only. Both
receipt and manifest verifiers must pin public key identities out of band.

## Predict / observe

**Observed with disposable local fixtures:**

- A >8 MiB journal containing **950 hash-linked rows** was signed by a
  fixture producer, verified and decrypted byte-exactly by the public-key
  path **without any producer HMAC secret**, then independently signed by
  a different fixture witness key.
- Explicit v2 receipt binds the signed producer-manifest SHA-256, preventing
  replacement of the producer provenance without invalidating the
  receiver's signature.
- Tests reject substituted ciphertext, tampered index, mismatched key,
  wrong node/path, foreign public key, altered manifest, replayed
  producer generation and witness receipt, invalid predecessor, malformed
  segment metadata and missing/wrong decryption permission.
- **56 targeted tests passed** across the new producer/receiver protocol
  and backward-compatible v1 external witness tests. Ruff and compilation
  checks passed. More comprehensive regression results and the new PR's
  GitHub CI are tracked separately, not assumed.

**What is NOT established:** The receiver does not yet hold a real
independently protected private key or non-rewindable receipt head. The
producer's generation and previous manifest SHA-256 are caller inputs;
without an independently secured counter/ledger, an entire old consistent
producer-and-receiver snapshot can still be replayed. A signature does not
turn writable ext4/CIFS into WORM. The isolated implementation is not
wired into the NAS publisher, no v1 archive is rewritten, and no real
production issuer or node task has been enabled.

## Release and physical acceptance

1. Review v2 schema stability, deterministic canonicalization, exact role
   separation, cross-version compatibility and strict input bounds.
2. Provision an external receiver service identity and a separately
   administered producer verifier key pin. Do not reuse claim-lease keys.
3. Provision independent append-only/WORM storage for receipt sequence,
   prior digest and producer manifest generation. Verify restart, deletion,
   replay, conflict and full-volume rollback on **two actual hosts**.
4. Provision separately protected SMB trace custody share and verify
   server-side ACLs, nonauthorized principal rejection, hardlink/no-overwrite,
   fsync/durability and out-of-band restoration on a bounded scratch
   object. The present multiuser `fileserver` Samba export remains denied.
5. Integrate only after these independent physical gates pass, with
   progress/backlog telemetry and key rotation. Keep the **7 MiB trace
   admission stop**, deployed trace issuer disabled and all historical
   archives/NAS5 recovery untouched.

Related: [external witness PR #234](https://github.com/scottjoyner/auto-assist/pull/234),
[SMB policy PR #219](https://github.com/scottjoyner/auto-assist/pull/219),
[NAS issue #127](https://github.com/scottjoyner/auto-assist/issues/127),
[production gate #120](https://github.com/scottjoyner/auto-assist/issues/120).
