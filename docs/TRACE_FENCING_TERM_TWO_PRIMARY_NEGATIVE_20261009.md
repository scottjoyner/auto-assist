# AssistX #148 — monotonic fencing authority and two-primary negative acceptance

**2026-10-09 | Research-only | Draft stacked after #252 | Production / distributed failover NO-GO**

## Why this slice

#252 physically proved worker isolation, PostgreSQL restricted admission, Neo4j transaction metadata, blackholed Bolt + worker/gateway death, verifier-only exact release, and fsynced closure before SQL release. It did **not** issue a monotonic term: `term=1` was a placeholder, with single-primary PostgreSQL. A copied or independently restored primary can admit concurrently without an external gate.

This slice tests whether a separately persisted authority can preserve a monotonic term, prevent an unauthorized/stale takeover, and conservatively retain uncertain physical work. It *also deliberately demonstrates* that cloning both the authority database and local checkpoint defeats this research design, and two independent real PG primaries sharing one epoch can each admit work.

## Implemented

### `tests/trace_fencing_term_authority_research.py`

- SQLite `BEGIN IMMEDIATE` serializes grants, binds, uncertain-state transitions, signed closure receipts and takeover; `PRAGMA synchronous=FULL`, no TTL-based release.
- Authority instance identity, monotonic term, owner and revision are stored with all pending attempts and replay hashes.
- A separate local checkpoint file stores a SHA-256 digest over authority state, attempts and consumed witness-receipt hashes. **Checkpoint is advanced and fsynced before the DB transaction commits.** Failure between them yields permanent mismatch and **fail-closed** recovery rather than replaying old state. The checkpoint is *not a quorum consensus term* or independently immutable.
- Unknown physical execution cannot be released: `RESERVED` blocks takeover and cannot be closed without a separately bound server identity/transaction; `STARTED` and `UNCERTAIN` require an Ed25519 witness-signed receipt with exact term, owner, attempt, server identity, generation and transaction ID; no client success/TTL shortcuts.
- A takeover to a different owner requires a separately signed Ed25519 **operator approval** for exact instance, expected term and successor. Old terms and replay approvals are rejected. Any unresolved attempt blocks takeover; only empty pending capacity allows increasing the term.
- Bootstrap is create-only and refuses any existing DB or checkpoint. Missing/invalid/mismatched checkpoint permanently denies new grants.
- **Not provided:** host-isolated keys, quorum membership, lock-service leases, witness assertion against actual graph in this module, server-side Neo4j stale-term rejection, production API integration or self-healing after local crash.

### `tests/test_trace_fencing_term_authority_research.py`

- Independent signer wrong-key, wrong-instance, stale term, changed server-generation/txid, tampered closure, replay, reserved-before-Bolt, orphan quarantine, no takeover with uncertain physical work.
- 12-thread and 7-subprocess cap contention proves local SQLite serialization; no overadmission under a *shared* authority database.
- DB-only rollback while local checkpoint survives => fail closed.
- Local checkpoint loss/tamper => fail closed.
- **Counterexample:** copy BOTH SQLite and checkpoint to a second directory; each authority independently admits the same operation at cap=1. This is a confirmed counterexample to local-anchor quorum claims.
- Operator approval forgery and old approval replay explicitly denied.

### `tests/probe_trace_fencing_two_primaries.py`

A manual `ASSISTX_TWO_PRIMARY_FENCE_RESEARCH=1` physical probe runs two fresh **PostgreSQL 17 Alpine SCRAM** primaries, each with the same research epoch and cap=1 on a private internal-only Docker bridge. Both independent server-enforced `admit` calls succeed, demonstrating global cap=1 **cannot** be proven from single-primary SQL replicated or instantiated independently without fencing.

Separately, the local research fencing controller denies a second grant at cap=1, refuses an operator-signed takeover with pending capacity, persists uncertainty after restart, and rejects a future term that has not actually been authorized. This does **not** retrofit server-side enforcement onto either PostgreSQL primary or Neo4j.

## Observed results — x1-370

- First focused 17 tests: **PASS**.
- Signed takeover and replay tests: 18 tests: **PASS**.
- All graph-entry + fencing focused tests before physical static guard: **66 passed** (includes real process contention).
- Opt-in *real two-primary* experiment: **PASS (~4.75 seconds, exit 0)**; BOTH PG primaries independently admitted despite intended global cap 1, while single local term-controller correctly held one slot and denied a second controlled operation.
- Physical failure-mode result is intentional **negative evidence** for the single-primary architecture, *not* proof of globally safe HA.
- Fixture teardown removed both disposable PG containers and their network; no production Neo4j, PostgreSQL, API, Redis or NAS instance modified. No graph read occurred in this new two-PG probe; the prior #252 graph transaction experiment is separate.
- `tests/test_trace_fencing_physical_guards.py` adds four static no-Docker safety guards. The existing offline-only CI workflow now includes both new fencing suites. Exact-head hosted CI to be verified after commit.

## Critical limitations and policy

1. **Cloned local authority copies still split brain.** Cryptographically signed operator takeovers cannot alone prevent a stale *copied database+checkpoint* from processing valid old-term grants. The operator key's signature authorizes a takeover only within one trust domain; it does not establish quorum.
2. **No server-side stale effects fence.** The gateway must validate ownership at each physical graph-entry edge and independently pin a Neo4j transaction identity. A term check before long-running work does not physically terminate old queries. New term takeover must remain blocked while any old-term effects are uncertain.
3. **Local checkpoint is not independently governed monotonicity.** File ownership, filesystem backup/restore, full authority cloning, crash consistency after data loss, and malicious host access can defeat it. No automatic reconciliation of a torn checkpoint is allowed.
4. **No production-role credential or signer escrow.** The same host's research coordinator can bootstrap both PG servers, create both key pairs, change Docker networks and access evidence. This is a research trust-boundary approximation.
5. **Physical test uses *independently initialized* PostgreSQL primaries sharing an epoch, NOT literal copied PGDATA.** It demonstrates the equivalent cap-splitting condition; a literal PG backup/restore, partition/rejoin and quorum election require separate acceptance.
6. **No production failover or API:** No 1/3/5/10 real concurrent authenticated graph clients, p95/p99, reverse-proxy auth/NAT fairness, cluster-generation pinning, replay custody across independent nodes or operational rollback is accepted.

## Next acceptance (minimum)

- Obtain an **independent quorum-based term allocator** (e.g. a three-voter Raft authority on genuinely separate failure domains). Demonstrate old-leader isolation, quorum-loss admission denial, monotonic term across restart, stale-owner rejection and partition healing. Never let a pre-commit term automatically free graph capacity.
- Enforce term/permit in the trusted gateway path **and** guarantee no worker/alternate graph credentials or network routes bypass it. Test old queries that keep executing after lease loss and hold capacity until an independent cluster-generation-aware witness attests closure.
- Move signed witness receipts into externally durable append-only custody with independent verifier/operator key ownership, revocation/replay registry and database-side release verification, not just process possession of verifier password.
- Carry forward #252's physical blackhole/crash and custody-failure negatives with 1/3/5/10 real authenticated API workers before considering any rollout.

**Disposition:** #148 OPEN; draft research only, distributed failover remains NO-GO; no deployed services changed.
