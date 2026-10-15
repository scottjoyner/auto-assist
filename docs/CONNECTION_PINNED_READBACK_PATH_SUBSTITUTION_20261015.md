# Pinned connection readback vs SQLite pathname substitution

**October 15, 2026 | x1-370 | RESEARCH / CRITICAL NO-GO**

## Security defect reproduced in previous draft

Auto-Assist draft #387 used a source SQLite BEGIN IMMEDIATE transaction to
exclude normal concurrent writers while a separate anchor consumed an
Ed25519 resource receipt. Its verification callback opened a NEW SQLite
read-only connection by resource pathname. In a disposable adversarial test,
a privileged local actor replaced that pathname using os.replace while the
source connection held its lock on the original inode. The new read-only
connection opened the replacement file, observed a fabricated receipt,
and advanced the anchor. SQLite's write-lock had never protected the
identity of this newly opened path.

## Narrow research remediation

This stacked draft reads BOTH resource observations through the exact
SQLite CONNECTION already holding BEGIN IMMEDIATE. It performs no further
pathname opens for security-gated readback. Each read still validates
the expected resource identity and matching receipt fields, including
term, sequence, generation, checksum, revision digest, and signed nonce.
This prevents redirecting the observation to a different database file
via a pathname swap after the original transaction opens.

The research wrapper also records (st_dev, st_ino) before connecting and
checks it after acquiring the lock, after any in-lock adversarial callback,
and after the anchor COMMIT. Replacement detected BEFORE anchor COMMIT
returns source_path_identity_changed with no high-water advancement.
Replacement AFTER anchor COMMIT raises
SOURCE_PATH_CHANGED_AFTER_ANCHOR_COMMIT_NO_RETRY uncertainty, so callers
cannot treat the observation as a durable security success or automatically
retry. That uncertainty does not undo a committed anchor transaction.

The source remains import-only. There is no production readback, network,
PostgreSQL integration, Neo4j permission, actual signing service, TPM
operation, model admission, host permission change or release action.

## Executed adversarial test matrix

- The original forged-substitute SQLite file test was run against #387:
  it **failed red** because the old code returned research success.
- With connection-pinned readback and identity sampling the same test
  **passes green** by denying the forged replacement before CAS.
- The same pathname substitution is denied even if the replacement
  contains matching legitimate rows: different inode is suspicious.
- A pathname substitution AFTER anchor COMMIT yields explicit uncertain
  no-retry; anchor readback confirms the prior CAS really committed.
- Ordinary source transactions and other prior negative controls still
  pass, including read-only receipt match/nonce, absent resource,
  invalid effect, ordinary writer lock, and signer+DBA collusion NO-GO.
- **NO-GO still reproducible:** a cooperating signer and DBA can
  fabricate resource effect+receipt BEFORE the pinned connection opens,
  with a matching valid test signature. The readback sees what it opened;
  no provenance is proven.
- **NO-GO still reproducible:** an administrator can replace a pathname
  and restore the original inode between stat samples. A synthetic
  swap-and-restore succeeds as a normal observation; path identity
  sampling is not cryptographic attestation, immutable filesystem policy,
  or proof that a file was never swapped.
- **NO-GO still reproducible:** after releasing the source write lock,
  direct privileged SQL may mutate the effect. Separate source and anchor
  transactions cannot guarantee effect-time finalization or cross-service
  durable causality.

## Reproduction

All test files and replacements are under pytest tmp_path; test signer
keys are ephemeral and synthetic.

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider tests/test_research_resource_fence.py

Passing synthetic tests mean the specific pathname confusion is fixed
*inside this disposable proof*, NOT that a real source can be trusted.

## Unchanged release HOLD

The next independently authorized step must establish a resource-side
authoritative identity and owned COMMIT/effect witness that a shared
host administrator, signer, source DBA, or pathname substitute cannot
impersonate; separately governed pinned roots and restore-resistant
anchor custody; revocation/fencing; and terminal readback for uncertain
COMMITs. Test both source and anchor crashes and privileged restore.
The current fleet operator's access across nodes is not independent
administrative control.

All existing NO-GO boundaries remain: Auto-Assist #156/#299, Context
cryptographic #46, graph #31, hosted CI #45, Router owner receipts and
iOS physical acceptance. No merges, deployments, production graph,
credential/key, network, TPM, model, failover or iOS actions.
