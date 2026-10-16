# Effect-time owner-term fencing: local SQLite protects only ordinary writers

**October 16, 2026 | Fleet Commander x1-370 | DRAFT / CRITICAL NO-GO**

## Why this follows #397 and differs from earlier source locks

#397 can authenticate a recovered cross-signed disposable member bundle,
source/anchor COMMIT readback and filesystem artifact. That check happens
*before* anyone uses the effect claim. Source-lock research #388 proved a
separate SQL write lock can protect a read through anchor consumption,
but the source becomes mutable when that lock is released.

This new experiment answers the *different* question: can a resource
executor enforce the currently authorized owner term and epoch
**within the same transaction that records the synthetic effect**,
rather than trusting a previously valid receipt or source read?

It does NOT execute a real-world resource effect. The only effect is a
tiny INSERT into a disposable SQLite `applied` table. All signers,
source/anchor, member outbox, effect journal, keys, owner terms and
backups are controlled by ONE local research administrator.

## Implementation

`scripts/research-effect-time-term-fence.py` bootstraps only a
specifically named, missing synthetic SQLite journal under the existing
pytest tmp_path marker. It has an authority record
(resource ID, owner ID, term, epoch, revoked flag) and applied-effect
records with unique sequence, nonce and challenge.

Each `execute_once` performs:

1. #397's full public-key / signed-bundle / source+anchor+artifact
   **read-only** check with an expected resource, prior hash-chain head,
   externally supplied bundle digest and two pinned public roots.
2. A deliberately exposed test hook **after** verification but
   **before** effect acquisition; another writer can rotate/revoke
   term and epoch in this interval.
3. `BEGIN IMMEDIATE` on a **separate effect SQLite journal**.
   Read the currently authorized resource owner, exact term, epoch
   and revoked flag. If mismatched, refuse even though the receipt
   verified earlier. This transaction serializes ordinary competing
   resource writers locally.
4. Repeat **full #397 verification** while holding that effect journal
   lock. This rejects changes to source, anchor, artifact or signed
   member bundle observed between initial verification and effect use.
5. Insert one uniquely keyed *synthetic* effect and COMMIT the effect
   journal as the same transaction that checked its local owner term.
   If a post-COMMIT callback loses the acknowledgment, throw typed
   `UncertainSyntheticEffectCommit(...NO_RETRY)`, never replay.

A read-only effect inspection returns only
`SYNTHETIC_EFFECT_OBSERVED_HOLD` and denies `retry_authorized`
and `release_authorized` in all paths. No router dispatch, live
provider loading, production agent or external network call exists.
The synthetic execution entrypoint is import-only.

## Actual local results

- **Stale term and owner revocation between receipt verification and
  effect transaction**: refused, no effect record. Same-term epoch
  revocation is also refused.
- Source COMMIT-generation mutation, member outbox/signature tampering
  and removal of the fsynced artifact after the first verification:
  second verification denies before INSERT.
- Two concurrent synthetic effect requests: exactly one committed
  record; later attempts receive `EFFECT_ALREADY_APPLIED_NO_RETRY`.
  An ordinary competing authority rotation during the held resource
  SQL write-lock is denied until the transaction releases.
- Explicit self-SIGKILL in a child **after INSERT but before COMMIT**
  leaves zero committed effects. SIGKILL **after COMMIT but before ACK**
  leaves exactly one effect and any second attempt is refused. Each
  outcome was read from a separate SQLite connection and passed
  `PRAGMA integrity_check=ok`. No host/service is killed.
- A lost-ACK Python exception after COMMIT similarly leaves one
  durable synthetic row and must be resolved by readback, not replay.

## Deliberately passing CRITICAL NO-GO counterexamples

1. **Privileged direct SQL fabrication:** An administrator writes a
   fictional `applied` row without calling this executor at all.
   Read-only journal readback sees a seemingly committed effect.
   This is not independent resource/physical effect provenance.
2. **Privileged owner-term rollback:** A local administrator directly
   rewrites the authority table back to a formerly authorized term,
   owner and epoch after revocation. A stale yet still cryptographically
   valid receipt passes the local effect check and records a synthetic
   effect. Local resource-owner history lacks an independent,
   restore-resistant monotonic root.
3. **Source/anchor/outbox remain outside the effect DB transaction:**
   the second #397 verification does not hold a shared physical or
   cryptographic lock on their storage. An administrator can change
   them *after* the second check and before or after effect COMMIT.
   The local lock fences only ordinary writers to this one journal.
4. **The signer's receipt does not independently certify owner epoch:**
   exact owner/epoch requirements are caller-supplied synthetic
   constants. No independent term grant, revocation signature,
   lease-expiry service, protected key or hardware-executor witness
   exists. It cannot prove authorization to execute an external effect.
5. **Split-brain and device durability:** two host/DBA administrative
   domains, TPM-backed nonexportable keys, append-only anti-rollback
   backups, actual power-loss/torn-write, lost network reply, host
   failover, and malicious signer/DBA collusion were not proven.
   SQLite `BEGIN IMMEDIATE` and SIGKILL cannot substitute for these.
6. **Already-committed effects are not undone by later revocation:**
   a normal owner-term transition is only prospective. There is no
   external system-wide revocation linearization barrier.

This research advances local concurrency and TOCTOU discipline, but
does not establish the independently governed resource executor
needed for production.

## Reproduce safely

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider \
        tests/test_research_effect_time_term_fence.py

All operations are confined to disposable `pytest tmp_path` SQLite
and read-only prior signed-bundle fixtures, with short-lived
synthetic Ed25519 test keys. The optional SIGKILL child requires
`ALLOW_SYNTHETIC_EFFECT_CHILD_KILL=TEST_CHILD_ONLY` and only kills
`os.getpid()` at two fixed transaction stages. No production
PostgreSQL, Neo4j, graph, model runtime, service, TPM, network or
Apple/iOS resources are accessed or changed.

## Next independent acceptance gate

Real resource-owned COMMIT and effect-time fencing need an
independently administered owner epoch, protected non-exportable
resource attestation key, and signed monotonically governed grants
that cannot be rewound by the source/anchor DBA or shared fleet OS
operator. Prove revocation/term transition against two adversarial
administrators, split-brain, stale effects, and uncertain COMMIT by
physical readback; keep all automatic retries disabled.

**Auto-Assist #156/#299, Context crypto #46 / graph #31 / CI #45,
Auto-Router owner-bound receipts, model admission and real iOS
release remain NO-GO. No merge, deployment, production graph,
credentials, network, TPM, failover, model or iPhone changes.**
