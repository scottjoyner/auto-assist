# Resource-side SQLite lock across external anchor CAS

**2026-10-15 · Fleet Commander x1-370 · Research-only / NO-GO**

## Why this experiment exists

[Auto-Assist draft #385](https://github.com/scottjoyner/auto-assist/pull/385)
proved that a signed receipt and two independent readbacks can be followed by
an actual resource mutation *before* the external anchor's COMMIT. This
disposable follow-up tests whether a resource-owned SQLite write transaction,
held through the anchor's receipt verification and CAS, blocks that race for
ordinary SQLite writers, without claiming independent resource authority.

The new import-only research module
`scripts/research-resource-fence-around-anchor.py` uses only fixture-created,
separate SQLite source and anchor files in pytest `tmp_path`. It opens the
existing source file with `mode=rw` and acquires `BEGIN IMMEDIATE`. Source
readback is made through a *different*, `mode=ro` connection, so it observes
only **committed** resource rows, and checks a corresponding receipt including
term, sequence, generation, checksum, revision digest **and nonce**. It then
calls #385's signed Ed25519 receipt consumer to atomically advance the
independent disposable anchor. The source write lock remains held through
that anchor COMMIT and is released by a source-side rollback (no source
mutations are committed by this wrapper).

This **does not make resource and anchor COMMIT atomic** and is not a
production/resource-owner security control. Even successful calls return
only `RESEARCH_SOURCE_WRITE_LOCK_AND_ANCHOR_CAS_ONLY`.

## Experiment matrix

- **YES, narrow guarantee:** A separate SQLite connection's attempt to
  UPDATE the source while its writer lock is held fails `database is locked`.
  The denial holds during readback and *after anchor COMMIT but before
  the source lock is released*.
- **Fail closed:** Missing source file is not silently created. Missing or
  nonce-mismatched source receipt, mismatched generation, missing anchor and
  pre-anchor callback failure do not authorize anchor advancement.
- **Uncertain outcome, no retry:** An interruption *after* anchor COMMIT
  but while the source fence is still held raises an explicit
  `UncertainReceiptCommit`. Anchor readback observes the advancement;
  retry from the old checkpoint is denied.
- **NO-GO, privileged SQL after lock release:** A direct writer can change
  the resource after the temporary write lock ends, leaving the anchor at
  the previously verified state.
- **NO-GO, colluding signer and DBA:** Privileged SQL can fabricate *both*
  source effect and receipt; a collaborating signer can sign the fiction.
  The otherwise-correct transactional observer and CAS return a research
  success.
- **NO-GO, inode/path replacement DURING a lock:** An adversarial test
  builds a second, coherent but fictional SQLite database and uses
  `os.replace` inside pytest's temporary directory while the source
  connection holds `BEGIN IMMEDIATE` on the original database. The
  independent readback opens the replaced pathname and verifies the
  attacker's fictional effect. The SQLite lock protects the original
  inode, **not the identity of the pathname, its filesystem owner, or
  the reality of an external resource effect**. This is a concrete
  reason an arbitrary source database path under shared OS admin cannot
  serve as independent commit evidence.

The regression suite intentionally **passes** when it reproduces those
NO-GO counterexamples. Passing tests are not a production clearance.

## Reproduction and boundaries

```sh
PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider \
  tests/test_research_resource_fence.py
```

All source/anchor databases and adversarial replacement paths are created
under pytest `tmp_path`. The module has no operational CLI, no real
network, Docker, fleet, PostgreSQL, Neo4j, credentials, hardware/TPM,
service routing, production model, graph mutation or deployment path.
Neither the source lock nor its readback establishes a real effect-time
term/owner fence, independently protected file identity, or multi-system
durability.

The smallest next physical proof requires an independently administered
resource system whose exact effect and commit receipt are *bound to the
authoritative resource identity*, not a replaceable path or DBA-controlled
table, along with externally governed monotonic high-water and trust-root
custody. Test privileged SQL, file/path replacement, snapshot restore,
signer/DBA collusion, failed/torn COMMIT and effect-time mutation. A
trusted terminal resource readback is required for uncertain outcomes;
never automatically retry. An independent reviewer must approve any
separately governed disposable infrastructure.

**All security/release HOLDs remain: Auto-Assist #156/#299, Context
cryptographic #46, graph #31 and hosted CI #45, Auto-Router owner-bound
receipts, and real-device iOS acceptance. No merge, deployment, credential,
TPM, network, production graph, failover, model admission or iOS change.**
