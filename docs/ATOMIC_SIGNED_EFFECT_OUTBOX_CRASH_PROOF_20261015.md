# Atomic witness issuance + complete signed bundle: one disposable SQLite transaction

**October 15, 2026, x1-370. Research only. Security remains NO-GO.**

## Why this follows PR #395

#395 can verify an Ed25519 resource-effect signature, a separately signed
witness certificate, and a witness-signed hash binding both certificates and
the fsynced effect SHA-256. But its *post-hoc bundle* was assembled after the
witness SQL checkpoint committed. A process could die after the checkpoint
was durably advanced yet before the signed effect bundle was assembled,
leaving an unrecoverable local delivery gap. A blind re-issue must remain
denied because the checkpoint was already consumed.

This source-only experiment removes that **local, single-member transaction**
gap: the entire preassembled signed bundle, unique issuance/challenge row,
and monotonic witness tip are inserted/advanced in the **same disposable
SQLite BEGIN IMMEDIATE / COMMIT** transaction. Each stored bundle has an
exact digest. The signing keys are *NOT* handled by the outbox module; it
only verifies fully signed, preassembled claims through #395's read-only
public-key verifier before writing the local witness transaction.

## The bounded implementation

- `scripts/research-atomic-effect-bundle-outbox.py`: import-only,
  explicit create-only outbox schema bootstrap for a new disposable member
  database, and a research-only `issue()` operation requiring:
  - the previous member term/sequence/hash linked to the proposed
    hash-chain tip;
  - an already complete #395 effect signature, member checkpoint
    certificate, and cross-signed effect-to-witness binding;
  - caller-supplied pinned *public* verifier keys, challenge, and time;
  - #395's O_NOFOLLOW effect artifact hash check and two-pass
    source/anchor terminal consumption check;
  - one SQL COMMIT containing `tip`, `issued`, and
    `signed_bundle_outbox` with exact immutable-at-SQL-level bundle bytes.
- `terminal_read()`: a new read-only SQLite connection checks DB integrity,
  tip, issuance challenge and signature, outbox digest and certificate
  consistency and returns an observational HOLD or NO-GO. It never
  dispatches, issues, repairs, signs, replays, acknowledges or authorizes
  execution. All results have `release_authorized=false` and
  `retry_authorized=false`.
- `tests/fixtures/research_atomic_outbox_child.py` is the explicit
  opt-in self-SIGKILL child. It receives only PUBLIC keys and a preassembled
  synthetic signed bundle under an already marked pytest tmp_path. It
  kills **its own PID** at one of two fixed stages, not the host or any
  service. It has no private signing material or operational entrypoint.

## Actual process-death and concurrency evidence

| Crash boundary | Source and anchor | Member tip | Issued row | Signed full bundle |
|---|---|---|---|---|
| SIGKILL after outbox INSERT, **before member COMMIT** | Previously committed fixture | Prior seq11 | 0 | 0 |
| SIGKILL **after member COMMIT, before ACK** | Previously committed fixture | Next seq12 | 1 | 1 (complete and hash-checked) |
| Normal success | Previously committed fixture | Next seq12 | 1 | 1 |
| Concurrent attempts at one member | Previously committed fixture | One seq12 winner | 1 | 1 |

The child process actually terminates via `os.kill(os.getpid(), SIGKILL)`.
A separate pytest parent opens a fresh read-only connection to verify
SQLite integrity, outbox content, and the exact original signed bundle.
Post-COMMIT ACK loss raises explicit uncertainty in the synchronous
research callback and a repeat issuance is rejected as already consumed.
Invalid signed bundles or missing artifacts refuse to write. Missing or
corrupt outbox entries when the member tip is advanced produce NO-GO.

## Critical NO-GOs reproduced by positive adversarial tests

1. **Full member backup restore erases the entire complete-bundle record.**
   After COMMIT, restore an old member database image. Readback now
   reports ordinary `OUTBOX_NOT_COMMITTED_HOLD`; the observer cannot
   establish from this database whether the bundle ever existed.
   NO AUTOMATIC RE-ISSUE. A separate restore-resistant history is required.
2. **Shared-admin fake COMMIT remains possible:** a privileged anchor DBA
   forges highwater/spent/consumed rows after source-only COMMIT, creates
   the correct synthetic effect artifact, controls two Ed25519 signing
   keys and supplies matching public pins. The entire preassembled
   package validates and is committed to the local outbox. One-DB
   atomicity does not authenticate authorized anchor COMMIT or physical
   effect causality.
3. **Privileged DBA tampering still wins:** a direct SQL UPDATE of the
   member tip or deletion of outbox records creates contradictory states,
   which a fresh local readback can detect, but privileged restore of
   the entire coherent member database cannot be detected locally.
4. **Cross-resource atomicity is unsolved:** resource, anchor, artifact
   and member outbox still span separate mutable domains. The test
   does NOT guarantee resource effect survival, term fencing or
   non-exportable keys, and no real independent resource executor
   was contacted. A privileged actor can mutate resource bytes
   immediately after a successful verification.
5. **Pre-signed envelope is synthetic:** the test packet is prepared
   before the separate outbox transaction. This experiment proves
   atomic *storage of complete bytes*, not atomic physical effect,
   independent signing issuance, cryptographic signer custody,
   global anti-replay or exactly-once delivery. Its verifier roots
   are caller-supplied under the same x1-370 test administrator.

## Reproduce

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider \
        tests/test_research_atomic_effect_bundle_outbox.py

All test databases, payloads, signing keys and process-death experiments
are local synthetic pytest fixtures. No network, real PostgreSQL,
Neo4j, production graph, credentials, TPM, model admission, automated
failover, phone or TestFlight changes. The filename/sentinel and environment
opt-in prevent the fixture from accidentally targeting unrelated paths.

## Next genuine independent proof

Require an independently owned, effect-time fenced resource signer that
attests **actual** authorized COMMIT, independently protected witness
monotonic history beyond backup restore and privileged SQL, pinned
public roots outside shared operator custody, and a terminal
reconciliation path that distinguishes rollback from never-committed
history without re-executing effects. Negative tests must include
privileged forged SQL/file/signature, signer collusion, effect mutation
between verification and use, split-brain term revocation and post-
COMMIT missing acknowledgments in independent administrative domains.

**DRAFT / NO MERGE / NO DEPLOY. Preserve Auto-Assist #156/#299,
Context crypto #46, graph #31, CI #45, Router owner-bound receipts,
model and iOS real-device release NO-GO boundaries. No changes to
production graph, credentials, network, TPM, model, failover or iOS.**
