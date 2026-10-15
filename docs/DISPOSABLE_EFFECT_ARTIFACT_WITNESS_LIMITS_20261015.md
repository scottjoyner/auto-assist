# Fsynced effect artifact versus forged source/anchor SQL

**October 15, 2026 | Fleet Commander x1-370 | DRAFT, CRITICAL NO-GO**

## Hypothesis after Auto-Assist #393

A witnessed COMMIT should correspond to a resource-side effect rather than
solely to administrator-controllable source/anchor/consumed-receipt rows.
PR #393 proved that an administrator can fabricate matching anchor SQL
without an authorized anchor CAS, leading a witness to sign a fictional
COMMIT. This experiment checks whether a separately recorded, fsynced
synthetic filesystem effect denies SQL-only forgery.

## Bounded implementation

The import-only module scripts/research-effect-artifact-gate.py creates a
specific tiny synthetic file only under a pytest tmp_path sentinel using
O_EXCL, O_NOFOLLOW, bounded payload bytes, fsync(file), fsync(directory).
It refuses empty payloads and overwrite. Issuance requires an externally
supplied expected SHA256, then opens the artifact with O_RDONLY|O_NOFOLLOW,
hashes its bytes and compares the original opened inode with its current
pathname before, during and after the witness transaction.

The gate repeats verification immediately before the #393 member's
SQLite COMMIT and checks after COMMIT. Detected pre-COMMIT substitution
aborts and rolls back; substitution detected after a member COMMIT produces
an explicit NO_RETRY uncertain outcome. Neither successful file fsync nor
a matching hash implies a real-world effect. This is import-only code,
with no production integration, operational CLI, or model/graph effects.

The original #393 certificate still cryptographically binds a synthetic
receipt digest, NOT this file's SHA256 as independently custodied authority.
In the test the caller controls the SHA256 expectation and both DB and file
permissions. This additional evidence check is not a resource-owner proof.

## Results and material NO-GOs

- SQL-only forged consumed anchor rows plus ABSENT external file: DENIED.
- Real fsynced matching artifact plus fixture-authorized consumed anchor:
  research-only member issuance accepted.
- Matching file but source-only COMMIT: denied by anchor consistency check.
- Tampered bytes, wrong SHA, missing file, symlink, replaced path, missing
  sandbox sentinel, attempted overwrite or empty payload: denied.
- File replaced after witness COMMIT: explicit uncertain NO_RETRY; local
  witness tip may already be advanced.
- **CRITICAL NO-GO**: administrator with file system permissions can create
  matching file and falsify expected SHA alongside forged SQL, reopening
  issuance. fsync != authority.
- **CRITICAL NO-GO**: same administrator can modify the file after it is
  signed and leave an advanced witness checkpoint behind. O_EXCL does not
  prevent privileged delete/recreate, and inode sampling does not
  cryptographically prove history or block swap-and-restore.

Tests that show issuance of forged effects PASS intentionally because
they demonstrate an unresolved safety failure. This research is not
production-ready.

## Reproduce

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider tests/test_research_effect_artifact_gate.py

Only disposable pytest tmp_path files, SQLite, tiny synthetic bytes and
ephemeral in-memory Ed25519 signing keys are used. No real PostgreSQL,
Neo4j, credentials, TPM, network, fleet service, Router effect, model
inference, Apple/iOS or production resource is accessed.

## Next security acceptance

An independently governed resource authority must attest actual committed
effect bytes or physical state, term/owner epoch, nonce and digest. Its
non-exportable signing key, verifier root, effect storage, backup/restore
controls and durable checkpoint must be outside the source/anchor DBA,
shared host and fleet operators' common administrative control. The
corresponding signed receipt must be causally linked to the authorized
COMMIT and non-replayable across restores. Negative tests must cover
privileged SQL false COMMIT, forged or swapped file, witness rollback,
colluding signers, revocation, split-brain, effect-time mutation and
unknown ACK, without automatic retry.

HOLD Auto-Assist #156/#299, Context #46/#31/#45, Router owner effects,
model admission and physical iOS acceptance. NO MERGE, DEPLOY, REAL
GRAPH, CREDENTIAL, TPM, NETWORK, MODEL, FAILOVER, OR IOS ACTIONS.
