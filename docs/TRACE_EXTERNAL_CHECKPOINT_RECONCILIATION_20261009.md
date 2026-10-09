# Trace custody: externally signed high-water reconciliation protocol

**Experiment:** October 9, 2026, ~17:48–17:55 EDT

**Disposition:** research-only, stacked on AssistX draft PR #242. No external WORM authority installed or production enabled.

## Failure window and conjecture

The receiver writes a v2 Ed25519-signed trace-custody receipt into an owner-private
local ext4 ledger and fsyncs it. The separate monotonic high-water authority
might fail before acknowledging the new sequence. The local receipt remains,
but the external head is still one receipt behind. Resetting or replaying
receipt publication would risk duplicate or contradictory custody outcomes.

**Conjecture:** A signed *external checkpoint* from a different Ed25519
authority can anchor an explicit receipt sequence and SHA-256 without needing
to trust the writable receiver ledger. The verifier must also pin the
checkpoint counter and prior signed checkpoint digest from a separately
controlled non-rewindable source. A read-only reconciler can distinguish
the exact one-receipt race from rollback and conflict **without modifying**
either ledger or granting durable external custody.

## Implementation and trust model

`src/assistx/trace_receiver_checkpoint.py` provides:

- Strict v1 canonical signed Ed25519 checkpoint contract binding authority
  identity, witness identity, producer identity/key ID, accepted receipt
  sequence and full signed receipt digest, external checkpoint counter,
  previous full signed checkpoint digest and issuance timestamp.
- Verification with a separately pinned public authority key and explicit
  expected checkpoint counter, predecessor digest and minimum timestamp.
  Passing expected values directly from an untrusted checkpoint is
  forbidden in deployment; the caller must obtain them from a genuinely
  independent, monotonic high-water source.
- `reconcile_local_receipt()`: read-only classification under the same
  receiver-ledger flock. Existing signed receipts are fully revalidated;
  exact matching external checkpoint returns `SYNCHRONIZED`.
  One extra locally fsynced receipt linked directly to the external head
  returns `PENDING_EXTERNAL_RECONCILIATION`, with
  `custody_acknowledged=false` and `independent_worm_proven=false`.
  External checkpoint ahead of local chain, conflicting digest, multiple
  locally unacknowledged receipts, missing/torn history, forged authority
  signatures, wrong predecessor and stale counter **deny**.

`sign_fixture_checkpoint()` is a test-only helper, **not** a deployed
authority or signing-key management service.

**A pending result must never be interpreted as success.** A separately
administered external authority must independently validate and durably
record the proposed receipt before it issues a newer checkpoint.
The returned `SYNCHRONIZED` state only says two verified local/signed
views agree, not that the external store has proven WORM properties.

## Predict / observe

Targeted fixture tests cover signed genesis, local-fsync-to-external-pending,
signed external acknowledgment, missing local ledger, validly signed but
conflicting receipt head, more than one unacknowledged row, wrong authority/
producer/witness identities, stale checkpoint high-water state, foreign key,
signature mutation, unknown fields and torn ledger.

**Physical xwing synthetic smoke (October 9 ~17:50 EDT):** Two separate
temporary Ed25519 keys generated on the controller signed a receiver receipt
and two mock external checkpoints. Only the signed receipt, checkpoints
and corresponding *public keys* crossed the authenticated SSH channel.
On xwing Python 3.12/ext4, the same source reconciler verified signed
genesis, classified one locally fsynced but externally unacknowledged row
as pending, verified the next signed external checkpoint to synchronize,
then denied deliberate deletion of the local disposable ledger against the
previously accepted external head. Temporary source and receipt directories
were removed. No NAS access, real encrypted archive, production task or
private signing key transfer occurred.

The independently controlled authority, monotonic durable storage and
external acknowledgment service remain **unimplemented**. This is a signed
protocol fixture + physical code-path test, not independent rollback proof.

## Remaining acceptance gates

1. Provision an independently administered authority and signing key,
   separate from producer and receiver control. Establish securely
   pinned public key, verified counter source, key rotation and escrow.
2. Independently persist checkpoint sequence and digest on an immutable
   or otherwise non-rewindable medium; test malicious rollback of both
   receiver ext4 and source NAS while the checkpoint remains current.
3. Implement explicit external prepare/commit acknowledgment and
   uncertain-in-flight recovery so no client treats an acknowledged local
   fsync as an externally committed receipt.
4. Provision the dedicated restricted Beelink SMB share and run bounded
   actual ciphertext write / independent decrypt / fsync / unauthorized
   principal negative tests.
5. Keep production trace issuer routes disabled, preserve old encrypted
   archive generations and NAS5 recovery, retain 7 MiB journal admission
   cutoff, and do not merge the draft stack as production authorization.

Related: [receiver ledger PR #242](https://github.com/scottjoyner/auto-assist/pull/242),
[NAS gate #127](https://github.com/scottjoyner/auto-assist/issues/127),
[production gate #120](https://github.com/scottjoyner/auto-assist/issues/120).

## Final local acceptance — 2026-10-09, ~17:55 EDT

**18 focused checkpoint tests passed; 306 combined trace/security/recovery tests passed**, one previously known Starlette/httpx deprecation warning. Ruff, Python compilation, workflow YAML parsing, source whitespace and CI registration passed. The physical xwing synthetic test passed all four expected checkpoint states. All signing keys were fixture-only and generated on the controller. Dedicated GitHub CI is a separate acceptance gate and must be inspected after publication.
