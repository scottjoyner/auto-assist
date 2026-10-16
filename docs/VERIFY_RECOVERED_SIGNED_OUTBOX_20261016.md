# Authenticate recovered signed outbox, not just its locally stored digest

**October 16, 2026 | Fleet Commander x1-370 | DRAFT RESEARCH / CRITICAL NO-GO**

## Narrow gap after #396

The #396 crash test proved that a single disposable member SQLite COMMIT
atomically stores three pieces of state: advanced witness tip, issued
certificate record, and a *complete signed effect bundle*. Its readback
checked SQLite integrity, outbox SHA-256 against the same database's JSON,
and witness-certificate signature bytes against the issuance row. It did
**not** itself verify the Ed25519 signatures or independently pinned
bundle digest, nor did it recheck the resource file, source transaction
and anchor rows at recovery time.

**Reproduced red control:** a privileged writer replaces the bundle's
witness-binding signature with invalid bytes and recomputes the
`bundle_sha256` column. The #396 readback still reports
`COMPLETE_SIGNED_BUNDLE_OBSERVED_HOLD`. A SHA-256 stored with its own
mutable data is a corruption check, **not source authenticity**.
The same is reproduced by replacing the resource-effect attestation
signature while preserving the original member issuance row.

## Additional, read-only verifier

`scripts/research-verify-recovered-effect-outbox.py` is an import-only,
public-key-only **post-crash** verifier. It requires an externally
supplied digest of the complete bundle as assembled before COMMIT, an
externally supplied expected previous member head hash, exact expected
resource/term/sequence/receipt, two 32-byte Ed25519 verifier public
roots, fresh challenge and time. It reads the existing #396 outbox
once, checks against **external** pins and expected head/receipt, runs
#395's full Ed25519 and artifact/source/anchor verification, then
reads the outbox a second time to deny a changed recovery snapshot.
All observations remain `*_HOLD` or `*_NO_GO`; every result sets
`release_authorized=false` and `retry_authorized=false`. No signed
bundle is returned as a dispatch or execution capability.

### Bounded observed test results

- Self-consistently rehashed outbox with invalid witness-binding or
  effect signature: prior #396 readback says complete; new verifier
  rejects against the independent digest. **If the digest is also
  attacker-supplied**, an invalid Ed25519 signature still rejects.
- Removed or altered resource bytes, source COMMIT effect or anchor
  consumed-receipt rows: deny at recovery-time re-verification.
- Mismatched/absent public root, previous head, signed-bundle digest,
  time-bounded evidence or challenge: deny.
- Actual child SIGKILL immediately before member COMMIT: only
  `OUTBOX_NOT_COMMITTED_HOLD`, with NO RETRY authorization.
- Actual child SIGKILL after COMMIT but before ACK: full bundle
  re-authenticates and produces
  `RECOVERY_CROSS_SIGNED_BUNDLE_OBSERVED_HOLD`, not release GO.
- Corrupted rows and changes between separate outbox observations
  produce NO-GO. No schema/data or signing-key mutation is performed
  by this verifier.

### Critical security NO-GOs remain

1. **Whole-member restore looks never committed.** Restore the member DB
   from a pre-commit backup after a real commit: the new verifier
   sees `OUTBOX_NOT_COMMITTED_HOLD`. It does not, and MUST NOT,
   infer that the effect can be retried. A signed original full-bundle
   hash does not by itself attest the latest monotonic checkpoint.
2. **All trust roots are test-controlled.** A shared administrator can
   replace the verifier's externally *supplied* digest, signing roots,
   expected effect, member history, resource, anchor and filesystem
   evidence. This test provides no independent custodian or protected
   latest-tip oracle; coherent fabrication remains possible.
3. **Not an atomic cross-system snapshot:** source and anchor are
   separately committed/read, the member is read separately, and
   the filesystem artifact can mutate immediately afterward. Even
   two equal outbox observations are not a lasting owner-term fence.
4. **Trust and revocation are NOT proven:** no independently governed
   attestor or signer keys, root rotation, high-water anti-rollback,
   host-root exclusion, effect-time fencing, revocation or real-world
   resource COMMIT evidence. No safe retry/delivery decision was added.
5. The original #395 bundle is preassembled outside #396's member SQL
   transaction. This change only checks it after crash; it does not
   prove the signatures were created in the actual resource COMMIT.

## Reproduce

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider \
      tests/test_research_verified_outbox_recovery.py

The test fixture uses #396's disposable source, anchor, witness and
filesystem artifact under pytest `tmp_path`, synthetic in-memory
Ed25519 keys, and self-SIGKILL child subprocesses constrained to their
own PID. No production graph, database, credentials, network, TPM,
fleet service, model runtime, Apple/iPhone or release effect.

## Next independent authority gate

Prove resource-owned, causally authorized COMMIT+effect attestation,
and a latest durable checkpoint/certificate protected against common
OS/DBA/backup administrators and signer/root substitution. Test whole-
witness restore, false resource-side COMMIT, late effect mutation,
split-brain/fencing, revocation and uncertain acknowledgments with
independent custody and NO AUTO-RETRY.

Security issue #299, key custody #156, Context #46/#31/#45,
Auto-Router owner effects, model admission and real iOS release gates
remain CRITICAL NO-GO. **DRAFT ONLY: NO MERGE / DEPLOY / PRODUCTION
GRAPH / CREDENTIAL / NETWORK / TPM / MODEL / FAILOVER / IOS CHANGES.**
