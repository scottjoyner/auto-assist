# Three disposable transactional witness ledgers — no independent authority

**October 15, 2026 | Fleet Commander x1-370 | DRAFT / CRITICAL NO-GO**

## Next hypothesis after Auto-Assist #392

#392 tested two-of-three Ed25519 signatures over hash-linked checkpoints
without each member maintaining its own durable issuance history. This
experiment implements **three separate SQLite per-member ledger files**,
transactionally appends signed checkpoint heads with local monotonic
sequence/prev_hash CAS, and tests ordinary concurrent issuance, uncertain
COMMIT and adversarial privilege/restore paths.

This is a local falsification harness, **not a distributed service,
consensus protocol, or independent administrative trust boundary**.
All three member files, all ephemeral Ed25519 keys, the resource database,
the anchor database and expected receipt evidence belong to one x1-370
pytest operator. No network/TPM/hardware, real resource or production action.

## Exactly what the synthetic append does

An explicit bootstrap creates a new member ledger only under a
pytest-created sentinel and exact synthetic per-member filename.
Existing history is never silently replaced. `issue()` opens an existing
member DB `mode=rw`, acquires SQLite `BEGIN IMMEDIATE`, checks the
member's currently stored term, sequence, nonce and head hash, and denies
stale, nonadjacent or hash-discontinuous proposed heads.

Before signing it reads the actual disposable source and anchor twice
and requires the *local #390 classifier* to return
`ANCHOR_CONSUMED_OBSERVED_HOLD`. The signing claim binds fixed witness ID,
term, adjacent sequence, resource receipt digest, hash chain, supplied
challenge, key epoch and time window. The new ledger head and a UNIQUE
issuance row commit together in one SQLite transaction. Only then is the
research-only certificate delivered.

The test signer is **an ephemeral Ed25519 private key passed in memory
from pytest**, never a protected key custodian. The function signature
must not be integrated into any production service. Missing or symlinked
witness DB, absent source/anchor, stale sequence, mismatched effect,
uncommitted anchor, duplicate issuance and unauthorized overwrite deny.
Lost acknowledgment AFTER member COMMIT raises a typed
`UncertainWitnessCommit: ...NO_RETRY` exception. A new read-only
connection observes the durable tip; ordinary reissuance is denied.

## Real disposable SQLite test results

- Three distinct member files issue matching hash-chain seq12 certificates
  only after the fixture's actual signed source+anchor COMMIT.
- Concurrent requests for the same member: **one winner, one denial**;
  each member's own ledger has a single unique issuance record.
- One source-only COMMIT or anchor DML before anchor COMMIT: **no issuance**.
- An exception before member COMMIT rolls back ledger and head together.
- After member COMMIT but before ACK: durable checkpoint exists even though
  caller sees only NO_RETRY uncertainty.
- Member ledger survives a separate anchor restore: previously signed
  seq12 evidence can detect the anchor's seq11 rewind.
- **Critical NO-GO:** restoring BOTH the anchor and the member-ledger files
  to pre-consumption versions resets history. Old signed seq11 quorum
  appears legitimate again. Local SQL monotonicity is not anti-restore.
- **Critical NO-GO:** direct privileged SQL resets a witness's tip and
  removes issuance rows. An old checkpoint can be issued again.
- **Critical NO-GO:** after a genuine SOURCE-ONLY COMMIT but no authorized
  anchor CAS, a privileged anchor DBA can directly update high-water and
  insert matching spent/consumed records. Two local readback passes see
  internally coherent rows, so an otherwise correct member witness
  **signs a false claim that anchor authority consumed the receipt**.
  This reproduces the causal-commit provenance gap independently of the
  exact key/signature mechanism.

The experiments intentionally PASS when they reproduce unsafe
counterexamples; they are evidence against production security GO.
Member SQLite transaction locks protect only ordinary writers during
their own connection lifetime. A host/DBA/backup administrator can
restore, replace, mutate or impersonate all three resources.

## Reproduction

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider \
        tests/test_research_durable_witness_issuance.py

Only disposable pytest tmp_path SQLite databases and in-memory private
keys. No fleet SSH probing, real Postgres/Neo4j, production authority,
credentials, network, model load, Router dispatch, TestFlight, or iPhone.

## Required next real security proof

Administrative independence must exist beyond different files,
containers, servers or cryptographic key identities: separate protected
key custody and root pin, independently protected monotonic latest head
across backups, and an authenticated source-side actual COMMIT/effect
receipt that cannot be fabricated with privileged DB rows or two
cooperating signers. The quorum must prove legal COMMIT causality under
term/fence authority, not just internal ledger consistency.

Never promote this code or auto-retry an uncertain receipt. Remain DRAFT
with NO MERGE / NO DEPLOY / NO PRODUCTION GRAPH / CREDENTIAL / TPM /
NETWORK / MODEL / FAILOVER / IOS ACTIONS. Auto-Assist #156/#299,
Kipnerter API crypto #46, graph #31, hosted CI #45, Auto-Router
owner-bound effects and iOS physical release HOLDs remain open.
