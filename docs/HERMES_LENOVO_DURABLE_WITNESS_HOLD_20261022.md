# Hermes durable Lenovo witness research — October 22, 2026

**Decision: HOLD, P0 #414 OPEN, draft PR #425.** This work adds a durable *reference implementation* and physical cross-host test, not an independently controlled production authority or an approved Hermes deployment.

## Material improvement

Previous Lenovo tests used a fresh in-memory synthetic signer reporting a constant high-water generation. This revision introduces `src/assistx/hermes_witness_ledger.py`, a bounded SQLite high-water reference backed by explicit transactions (`BEGIN IMMEDIATE`, `synchronous=FULL`, `journal_mode=DELETE`) in a private directory and a private database file.

The reference ledger uses exact owner identities and SHA-256 checkpoint/manifest bindings. Only a **separately authorized publisher** may enroll and advance an owner record. Queries cannot auto-enroll, publish or advance high-water. A lower generation is rejected; a same-generation request with conflicting manifest/checkpoint is rejected; idempotent matching publication is permitted. Owner revocation is sticky even after process restarts, and revoking an unknown owner fails closed. Responses are signed with the configured Ed25519 key and reflect the durable generation and revocation state.

The source is a library **without any production listener, service unit, auto-start configuration, key-generation endpoint or enrollment API**. The caller must independently authorize publication and custody; the library itself cannot establish that authorization.

## Exact physical test

Command, opt-in research only, run nonroot on x1-370:

    PYTHONPATH=src python3 tests/hermes_durable_witness_offhost_canary.py --physical-disposable-lenovo

The canary sends exact locally hash-pinned library source **in SSH process arguments** using the already-trusted Lenovo SSH alias; it does not install source files or start persistent processes. Lenovo creates a private **temporary test directory** containing SQLite, a newly generated **test-only 0600 signing key**, and its journal files during use. The private key remains on Lenovo and is removed with that directory at completion. This differs intentionally from the earlier memory-only signer: a short-lived test key must persist across separate process invocations to verify restart continuity.

Fleet Commander physically verified, via distinct SSH/Python processes:
1. Explicit owner enrollment at generation 2 persists in SQLite.
2. A fresh generation-2 signed challenge is accepted after remote process restart.
3. An explicit attempt to publish generation 1 is denied.
4. A valid generation-1 challenge is denied against retained generation 2.
5. A revocation is persisted and a new process returns a signed revoked response.
6. The test directory, SQLite ledger, signing key and sidecars are deleted; no resident service remains. Production Hermes original image was verified unchanged before and after.

**Result:** physical durability PASS, monotonic negative checks PASS, revocation/restart PASS, cleanup PASS. Offline tests cover explicit enrollment, concurrency conflicts, idempotence, sticky revocation, malformed records and challenges, wrong-owner access, symlink/private-directory requirements, durable reopen and corrupted database failures.

## Deliberate counterexample: whole-ledger rollback

A negative unit test saves the entire SQLite file at generation 1, publishes generation 2, and then restores the original database bytes. The ledger accepts generation 1 again. This is **expected** without a *second* immutable high-water authority: SQLite durability and separate physical hosts do not make records rollback-resistant when one administrator can overwrite both the database and its signer.

Therefore **do not call this independent custody, cryptographic rollback resistance, append-only witness, real owner-bound authorization or production-ready fencing**. Lenovo and x1-370 share fleet SSH credentials; Lenovo's temporary signer and database are under the same operator, not a separate administrative trust domain. The test proves an implementation and a durable process boundary only.

## Required to close the release HOLD

Provision a separately administered signer/ledger with immutable or external append-only checkpoint publication and acknowledgment, revocation/fencing semantics across partitions, nonshared credentials, signed key rotations, owner-bound grants and test receipts. Run negative multi-host tests after rolling back the **entire** worker and witness SQLite contents. Separately repair Hermes' remaining **16 production binds including nine writable**, two writable protected SSD ancestor aliases, broad NAS-labelled fallback path, mutable Git mounts and private SSH mounts, and qualify narrow approved RO input plus isolated RW output, a signed release image and rollback.

**No production Hermes restart, mount alteration, real-model call, NAS transfer or protected corpus read was performed.** Keep PR #425 draft and P0 #414 OPEN.
