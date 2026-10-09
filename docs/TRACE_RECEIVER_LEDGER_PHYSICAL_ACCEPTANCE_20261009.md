# Receiver-owned trace receipt ledger — ext4 experiment and hold

**Evidence time:** 2026-10-09 ~17:14–17:21 EDT (America/New_York)

**System boundary:** isolated review code and disposable xwing scratch only;
no production service, real NAS publish, signer provisioning, issuer route,
journal mutation, or historical archive deletion.

## Hypothesis and test design

Earlier [PR #237](https://github.com/scottjoyner/auto-assist/pull/237)
proves independent Ed25519 **producer provenance** plus **receiver-signed**
receipt correctness. Without persistence, however, a receiver may accept
a signed but stale receipt after local state is replaced or copied.

Hypothesis: one receiver-owned fsynced append-only *local* ledger with a
single-writer file lock, strict owner-private files and re-verification of
every Ed25519 signature can reject stale, conflicting and missing receipt
history **when compared to a separately held, trusted sequence/head**.

`src/assistx/trace_receiver_ledger.py` implements:

- No directory creation in existing service or NAS paths; caller supplies a
  pre-existing owner-private `0700` root.
- A `0600` local lock file, owner/regular-file/link-count check and
  `flock(LOCK_EX)`; no unlink/reinitialization of older receipts.
- Existing receipt chain replay from byte 0, each v2 receiver Ed25519
  signature checked against pinned witness/producer IDs and a canonical
  `previous_receipt_sha256` link and monotonically increasing sequence.
- **Independent external expected next sequence and last full signed receipt
  digest** required for every append. The two expected values are never
  inferred from the incoming receipt.
- Strictly bounded `16 MiB` ledger and receipt-size limits, minimum free
  disk threshold; fail closed on torn rows, invalid signatures, bad modes,
  unexpected genesis, stale metadata, wrong node/index/producer-manifest
  digest, old anchor and conflicting concurrent writers.
- Append then `fsync` receipt file and parent directory before returning a
  local receipt head. Return flags explicitly state
  `receiver_local_fsynced=true`,
  `independent_anchor_fsynced=false`,
  `worm_storage_proven=false`.

## Observed test evidence

**Local:** 12 targeted tests passed for two sequential chained receipts,
concurrent same-head contenders (only one admitted), missing ledger while
externally expecting sequence 2, replay, corrupted signature, torn record,
unsafe file modes/symlink, wrong node and producer digest. Python compile,
Ruff and YAML workflow registration passed.

**Physical xwing, read/write DISPOSABLE namespace only:** xwing Python 3.12,
Ed25519 and ext4 `/dev/nvme0n1p5`. A detached synthetic v2 signed
receipt and its fixture public verifier key were generated on the controller,
then transferred using an authenticated SSH channel to a temporary private
working directory on xwing. The source verifier/ledger module ran on xwing,
persisted a signed receipt with `fsync`, re-read its signature and digest,
then denied replay and denied a deleted ledger when a previously known
external head was supplied. The test's scratch working tree and receipt
file were deleted by its own cleanup handler.

Expected and observed machine flags:
`local_fsynced=true`, `replay_denied=true`,
`deleted_history_denied_by_pinned_external_head=true`,
`scratch_cleaned=true`, `independent_worm_proven=false`.
**No HMAC key, producer private key, receiver private key, actual archive,
or remote NAS write occurred.** This was a synthetic receipt storage test,
NOT proof that the receipt arose from a physical archive restore.

## Why this is not yet independent custody

**Local ext4 remains mutable by its owner/root.** The caller can supply an
outdated `external_expected_previous_digest` after a coordinated rollback,
and the module cannot tell if the independent high-water source has also
been restored. Its append and updating the *external* high-water anchor
are not one atomic transaction. If an outage occurs after local fsync but
before the external head changes, retries correctly fail closed and must
be reconciled with independently witnessed evidence, not by resetting the
ledger. The receiver is still controlled through the shared fleet
operator identity; this is **not a distinct security principal**.

To establish WORM/anti-rollback, provision a **separately administered
append-only immutable receiver** or storage with a verifiable external
high-water checkpoint, explicit expected sequence and predecessor,
no-delete retention, durable acknowledgments and recovery from
post-local/pre-external crashes. Protect the actual witness signing
private key and key-rotation history outside controller/producer
authority. Test copying both NAS ciphertext and the local receipt ledger
back to a mutually consistent prior snapshot; it must *still* be denied
against a live independent high-water witness.

## Next operator gates

1. Choose an independently administered witness authority with a dedicated
   service identity, restricted signer and external high-water store; do
   not reuse xwing's shared fleet user as independence evidence.
2. Prove failed external head update, corrupt local ledger, cross-process
   concurrency, reboot and full-volume rollback with independent checking.
   Commit-before-ACK repair requires a custody protocol with proof, not a
   writable-ledger reset.
3. In parallel provision the separated Beelink SMB share with dedicated
   Unix/Samba principal and ACL as specified in [PR #219](https://github.com/scottjoyner/auto-assist/pull/219);
   test an unauthorized principal, real hardlink/no-overwrite behavior,
   fsync and out-of-band decrypt/restore.
4. Only then integrate producer manifest from #237, physical receiver
   decryption from #234, PREPARED/ACK from #135 and locally fsynced receipts.
   Keep trace admission bounded at 7 MiB and real issuer routes disabled.

**Authority:** Test passage is no basis to merge/enable a production
exporter or change the existing fileserver, NAS5 recovery, running workers,
signing keys or preserved four-generation archives.
