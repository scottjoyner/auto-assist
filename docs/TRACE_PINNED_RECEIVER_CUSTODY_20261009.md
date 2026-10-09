# AssistX #148 — Pinned receiver identity and conservative receipt custody

**2026-10-09 | DRAFT / SOURCE-ONLY / NO PRODUCTION QUERY AUTHORIZATION**

This experiment builds on [draft PR #218](https://github.com/scottjoyner/auto-assist/pull/218), which demonstrated a physical blackholed Bolt connection, independent Neo4j termination, and an Ed25519 envelope generated in a separate receiver child. The remaining weakness was important: the receiver's public key traveled alongside the receipt, and the same signed receipt could be verified repeatedly.

## New source-owned research boundary

`src/assistx/trace_receiver_custody_research.py` introduces an **independently supplied Ed25519 public-key pin** and a persistent **single-host SQLite observation-custody journal**.

- The receiver public key and the expected epoch/transaction/query/token/nonce are constructor or caller inputs from a *separately trusted source*. **An untrusted receipt's own public key is not an authority.** This module does not provision such a source in production.
- Only preexisting custody files can be opened (`mode=rw`). Creation is a separate, explicitly disposable `/tmp/assistx-trace-custody-test-*/receiver-custody-test.sqlite` bootstrap; it refuses reuse and production paths. The file must be a regular, single-link, private-permission file; a live instance pins its device/inode.
- The journal enforces unique receipt nonce, admission token, graph+server transaction identity, receipt digest, and sequence. `BEGIN IMMEDIATE` plus `synchronous=FULL` serializes local processes.
- A monotonic SHA-256 commitment chain binds every receipt digest to its previous head and sequence; each open or write recomputes and checks the full chain. Caller-supplied **external checkpoint (sequence, hash)** must match the entire persistent state. If an operation committed but its updated checkpoint was lost, reopening with the old checkpoint is **denied**. No auto-reset, repair, TTL, replay acceptance, or optimistic slot rearm.
- Accepted result means **one witnessed observation was durably recorded**. It is explicitly `recorded_no_release`; this module has no slot release, admission, dispatch, source routing, external provider, runtime API, or authority-grant method.

## Actual verification

x1-370 native isolated worktree:
```
PYTHONPATH=src python3 -m pytest -q \
  tests/test_trace_receiver_custody_research.py \
  tests/test_trace_receiver_evidence_research.py \
  tests/test_trace_durable_ledger_research.py \
  tests/test_trace_physical_probe_guardrails.py
# 95 passed
```

Included negative controls:

- A receipt signed by the *wrong* key, an empty key, or a tampered receipt is not a valid credential, even if its envelope supplies a public key.
- Exact epoch, graph container, server transaction, token, query reference, and receiver nonce are required, and repeated nonce/transaction/token consumption is rejected.
- A reused old checkpoint cannot reopen after a successful commit, including an ACK lost after commit. On a local snapshot rollback, the external **newer** checkpoint detects the stale database.
- Lost/removed ledger, unsafe world-readable permissions, damaged SQLite tables, interrupted chain commitment and noncontiguous sequences fail closed.
- Five independent spawned processes contending for the **same receipt** record it **at most once** on a surviving shared local file.
- **Explicit counterexample: two independent copies of the same SQLite file and trusted checkpoint can each accept the same receipt.** Local uniqueness is NOT global consensus.
- **Explicit counterexample: a dishonest holder of an otherwise pinned signing key can sign a fabricated report, and a structurally valid signature can be recorded without any real Neo4j observation.** Cryptography does not make a false claim true.

The extended GitHub research workflow executes these tests on the exact source head. It does **not** run a physical Docker Neo4j probe or provision production signing keys.

## What remains blocked

1. No independent operator-managed key provisioning, durable private-key custody, revocation/rotation, signer attestation, or key escrow. In this test, an ephemeral fixture key stands in for a configured pin.
2. The **updated checkpoint is returned to the caller**, but no external trustworthy checkpoint service or rollback-resistant consensus exists. Reverting both the local journal **and** the externally pinned checkpoint can resurrect consumed receipts.
3. Neither cross-host copied-ledger split-brain nor graph server restart/ABA/consensus is resolved. A local transactional receipt journal cannot fence separate nodes.
4. Physical termination facts remain witness claims. Signed evidence should be cross-checked against a separately owned server instance and an independently evaluated observed terminal lifecycle, not just the signer's statement.
5. **No automatic release of physical query capacity.** The earlier durable admission ledger remains nonexpiring until a *separate*, yet-to-be-proven trusted release protocol is authorized.
6. Authenticated multiworker API/Neo4j 1/3/5/10 staging, long blackhole during management-plane failure, physical ingress spoof-header #149, p95/p99, privacy/role scope and operator rollback are all unproven.

**Disposition:** stronger, reproducible single-host receipt-custody research; hard fleet-wide #148 and #149 both remain OPEN/NO-GO. Do not merge this draft or activate production based on synthetic CI.
