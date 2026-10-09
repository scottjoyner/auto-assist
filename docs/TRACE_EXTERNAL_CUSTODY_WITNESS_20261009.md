# External trace custody witness — cryptographic contract (offline)

**Evidence checkpoint:** 2026-10-09, America/New_York

**Status:** source and fixture tests only. This does **not** create an external service, protect a private key on an independent host, or authorize NAS publishing/production trace execution.

## Why this slice exists

The existing local `published.jsonl` HMAC witness and
`publication-intents.jsonl` PREPARED/ACK state in PR #135 improve
crash consistency, but cannot detect replacement of **both** the controller's
local witness history and NAS ciphertext with a previously consistent snapshot.

A separate custodian must **independently decrypt and verify** an encrypted
NAS archive and issue an **Ed25519-signed receipt** whose signing key is never
available to the producer. Subsequent clients must verify this receipt
against the custodian's public key AND an independently durable, already
pinned monotonic sequence and prior receipt digest. Merely reading the
expected values from the receipt is an insecure self-attestation.

## Exact receiver contract

`src/assistx/trace_external_witness.py` provides two operations:

1. `attest_restored_archive` — a receiver-side helper, requiring a private
   Ed25519 signer and decryption permission, verifies an owner-private,
   node/journal-digest-named archive, authenticates its encrypted index,
   decrypts all segments and checks the complete trace hash chain and
   byte-exact journal SHA-256 before signing a canonical receipt.
2. `verify_external_receipt` — independently checks the exact v1 schema,
   pinned witness public key and identity, node, journal digest, ciphertext
   index digest, positive record count, signed journal head, expected
   monotonically increasing receipt sequence, previous full signed-receipt
   SHA-256 and caller-supplied minimum observation timestamp.

The receipt's previous digest starts at a known genesis constant and
subsequent digests link to the full prior receipt including its signature.
A verified receipt returns its own digest so a *separate custodian* can
advance an immutable high-water mark. Replaying a prior receipt against
this pinned mark denies. If the high-water mark itself is restored from
the potentially rolled-back source directory, the scheme **cannot**
detect that complete rollback.

## Predictions and observed fixtures

Prediction: a remote independently signed receipt should be rejected after
any payload, signature, node, index, journal or signer alteration, or after
a sequence/previous-digest replay. Receipt emission must fail when the
receiver cannot independently restore the encryption, when ciphertext
index digests differ, or when the archive namespace uses an unexpected
node or symlink.

Observation: **18 focused offline tests passed** using temporary
owner-private directories, GPG-encrypted >8 MiB synthetic trace history,
fixture-only Ed25519 private keys, and local pinned verifier expectations.
Positive tests verify 950 hash-linked records and a second chained
receipt; negatives cover wrong keys, wrong node/index/digest, changed
observation time, tampering, wrong verifier, replay, malformed signature,
unknown fields, missing decrypt permission and path substitution.
No real NAS share or production issuer is involved.

## Separate physical acceptance and hard blockers

1. Choose an external receiver host controlled separately from Beelink
   and the production AssistX controller. Protect its Ed25519 private key
   behind a service-specific account/file policy, escrow it out of band,
   and pin the public key plus explicit witness identity on verifier nodes.
2. Build an authenticating **receiver-owned** append-only service that reads
   the encrypted archive from a dedicated 0600/0700 SMB or other securely
   provisioned custody namespace and performs the full restoration itself.
   Do **not** let a sender simply submit an unverified digest for signing.
3. Store monotonic receipt sequence and prior receipt digest on a
   separately durable, non-rewindable medium. Prove reboot, crash,
   missing predecessor, replay, signature substitution, full copy rollback
   and conflicting concurrent writers with an independent verifier.
4. Provision the dedicated server-side SMB share/Unix principal/ACL per
   `docs/TRACE_SMB_CUSTODY_READINESS_20261009.md`. Today's multiuser
   `fileserver` share remains denied, and no actual encrypted object
   was written this pass.
5. Only after independent receiver/signing and real CIFS write/drill are
   accepted should the NAS publisher acknowledge independently durable
   custody. The current 7 MiB source journal backpressure, all issuer
   disablement and no-shell restrictions remain.

**Key-isolation design gap:** The present bundle's index and segment
metadata are authenticated with a symmetric HMAC key, so the independent
receiver currently needs that *verification secret*, plus a decryption
secret, to verify the bundle. Giving a receiver a producer's HMAC key
could expand forgery authority. Before production, introduce an asymmetric
producer-signed bundle manifest or a strictly scoped verification mechanism,
with independent per-role encryption and key-rotation policy. The receiver's
Ed25519 witness private key must never be exposed to the producer.

This is cryptographic contract acceptance, **not evidence that WORM
storage exists**. It should stay stacked in draft review until separately
controlled custody and full physical negative tests are demonstrated.

Related: [SMB readiness PR #219](https://github.com/scottjoyner/auto-assist/pull/219),
[NAS custody issue #127](https://github.com/scottjoyner/auto-assist/issues/127),
[production gate #120](https://github.com/scottjoyner/auto-assist/issues/120).


## Test checkpoint — 2026-10-09

The combined regression suite completed with **237 passed / one unrelated
Starlette/httpx deprecation warning** before the additional explicit
ciphertext-substitution fixture was appended. The final witness-focused
suite then passed **18/18** including that new case. Both Ruff and Python
compilation passed; dedicated GitHub CI on this new review commit must be
checked separately after publication. No key files from this experiment
may be promoted as real receiver credentials.
