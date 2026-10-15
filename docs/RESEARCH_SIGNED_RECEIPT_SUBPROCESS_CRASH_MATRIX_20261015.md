# Real child-process crash boundaries for signed receipt consumption

**October 15, 2026 | Research-only / NO-GO.**
**Target:** stacked on Auto-Assist draft #388; this is not production code.

## Motivation

Earlier #385 tests modeled lost acknowledgments and SQLite COMMIT exceptions,
but not an operating-system process dying at precise transaction boundaries.
A missing ACK must not be equated with a rolled-back resource or anchor.

This slice adds a *strictly synthetic* child subprocess that SIGKILLs **only
its own PID** at one of four predetermined checkpoints. The parent test
creates two separate SQLite files under pytest tmp_path. The child refuses
to operate without an explicit environment opt-in, fixed disposable file
names, a parent-created sentinel, and existing resource/anchor databases.
There are no SSH, Docker, production PostgreSQL/Neo4j, real services,
credentials, TPM, graph, models, networking, or device interactions.

## Exact experiment

A research-only, optional hook in
`scripts/research-consume-asymmetric-receipt.py` exposes two checkpoints:
after high-water+spent-ledger updates but **before** anchor COMMIT, and
after a successful COMMIT but **before** acknowledgment. Only the opt-in
test worker uses this hook. Synthetic signer keys remain within the
child's memory and never enter a production trust store.

| Child SIGKILL stage | Resource rows | Anchor sequence | Spent challenge + receipt ledger |
|---|---:|---:|---:|
| Before resource COMMIT | 0 | 11 | 0 |
| After resource COMMIT, before anchor | 1 | 11 | 0 |
| After anchor UPDATE/INSERT, before anchor COMMIT | 1 | 11 | 0 |
| After anchor COMMIT, before ACK | 1 | 12 | 1 |
| Normal completion | 1 | 12 | 1 |

Each result is checked **after the child process has terminated**, from
fresh read-only connections. Tests also run SQLite integrity_check.
The parent never retries after a killed child.

The distinction matters: `anchor_after_commit_before_ack` has no
observed success response but the durable SQLite checkpoint and spent
challenge are visible at sequence 12. Retrying the effect because the
child died would be unsafe.

Run CPU-only research tests:
```sh
PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider \
  tests/test_research_receipt_subprocess_crash_matrix.py
```

The child performs real SIGKILL, not merely raising a Python exception,
but this does **not** emulate power failures, partial storage-device
writes, host restart, fsync loss, split-brain networks, independent key
custody, or separately governed administration. It does not validate
that a signed receipt corresponds to a real external resource effect.

## Critical remaining NO-GOs

1. Source and anchor COMMITs are separate transactions. A crash between
   them leaves resource sequence 12 but anchor sequence 11, requiring
   a separately authorized terminal readback protocol, not blind retry.
2. Loss of acknowledgment after anchor COMMIT leaves sequence 12 with
   spent challenge recorded. The caller cannot infer this from the
   missing success response alone.
3. Privileged DBA or backup restore can roll back both high-water and
   spent receipt/challenge tables, reopening prior receipts.
4. A colluding signer and resource DB administrator can fabricate
   matching signed effects; a trusted resource owner and key custodian
   are not established by these subprocess tests.
5. Effect-time TOCTOU and shared host identity manipulation remain
   unresolved. The source write-lock proof in #388 only mitigates one
   pathname substitution, not administrative authority.
6. Only real host- and key-custody separation plus an independently
   controlled monotonic effect/receipt witness could satisfy a
   production-grade acceptance gate.

**Keep all NO-GO, no-merge, no-deploy, no graph, credential, network,
model, failover, or iOS release boundaries.** No automatic retries of
uncertain COMMITs and no promotion from passing synthetic tests.
