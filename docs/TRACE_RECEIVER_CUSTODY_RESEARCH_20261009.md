# #148 — Pinned receiver trust and local replay custody research

**2026-10-09. RESEARCH ONLY / NO PRODUCTION AUTHORIZATION.**
Stacked on draft [PR #218](https://github.com/scottjoyner/auto-assist/pull/218). This branch changes only source-owned unused research code/tests and the offline research workflow. No endpoint, middleware, model router, real Neo4j/Redis, NAS, Tailscale, credentials, keychain, or live API was changed. In particular it **never calls the durable physical admission ledger's release method**.

## Motivation and previous evidence

- Draft #218 proved a *real* blackholed Bolt stream can leave a server-side Neo4j transaction running while the client cannot receive replies; a separate receiver process subsequently observed, terminated and signed a synthetic closure report. The local admission slot remained occupied. The receiver's ephemeral public key was returned alongside the signed report, so there was no independent trust anchor or persisted nonce custody.
- A signature only verifies the signer and bytes, **not** the truth of a remote query-termination assertion. The earlier deliberate negative control showed a dishonest signer can produce a mathematically valid but factually false release claim. Retain that test and do not broaden the claim.
- The next gate is an independently pre-pinned receiver public key and atomic local replay recording. Even if successful, this is **not** distributed anti-rollback or production key custody.

## New module and properties

`src/assistx/trace_receiver_custody_research.py` exposes only research helpers:

- `bootstrap_disposable_custody()` creates an exclusive `receiver-custody.sqlite` only in an `/tmp/assistx-receipt-test-*/` directory, with 0700 parent / 0600 DB permissions, and rejects overwrite or rearm. **Never invoke at runtime or from the API.**
- `LocalReceiverReceiptCustodyResearch(path, epoch, graph_id, independently_pinned_public_key)` requires a nonempty **caller-pinned 32-byte Ed25519 receiver public key**. It does not accept a public key from the received envelope. The preexisting SQLite store binds exact `epoch`, `graph_id`, schema and SHA-256 fingerprint of the pinned key.
- Local store uses SQLite URI `mode=rw`, `BEGIN IMMEDIATE`, `synchronous=FULL`, exact inode check, no TTL, no automatic expiry, no recreation when missing or corrupt. A changed file or wrong pinned graph/key/epoch fails closed.
- `record_observation_once()` first checks the existing strict Ed25519 envelope and **independently expected** token, query ref, Neo4j server transaction ID, graph identity, epoch and nonce, then transactionally records a canonical digest. The database enforces uniqueness on `(epoch,nonce)`, `(epoch,token)` and `(epoch,graph,server transaction ID)`. A second request returns `duplicate_or_replayed_receipt` and never reopens admission.
- The record result is `recorded_only_not_released`. The class has **no `release` or `admit` method**, never imports the admission controller, and does not call `acknowledge_remote_closure`. The helper cannot, by itself, authorize any real graph operation or physical slot reclamation.
- Expected binding fields must come from a separate authoritative registration in a future design; **a caller that controls these expectations can lie**. The module does not provide server-side identity proof or a key enrollment protocol.

## Executed native acceptance, x1-370

Detached checkout `/home/scott/git/.worktrees/assistx-receiver-replay-20261009`.

```bash
python3 -m pytest -q \
  tests/test_trace_receiver_custody_research.py \
  tests/test_trace_receiver_evidence_research.py \
  tests/test_trace_durable_ledger_research.py \
  tests/test_trace_physical_probe_guardrails.py
# 91 passed
```

- Local recording survives construction of a new custody instance and the same receipt is refused.
- Spawned-process concurrent attempts in waves of **1, 3, 5, 10**: one and only one record wins, with no duplicated accepted receipt against a shared live local DB.
- Valid signature from a different key denied; modifying graph, transaction, token, epoch or nonce denied.
- Token replay with a new nonce and newly signed message denied; server transaction reuse with another token denied conservatively.
- Missing DB and swapped inode fail closed, without recreating a file. Corrupted epoch/schema refuses recording.
- Integration negative verifies a completely separate local physical admission ledger still has **occupancy=1**, returns `full`, and remains untouched **after** the trusted receipt was recorded.
- **Explicit passing signed-false-evidence counterexample:** a dishonest holder of the pinned signing key can sign invented Neo4j termination assertions and the local custody store will record their bytes; this is NOT remote-termination truth and must never trigger slot release.\n- **Explicit passing split-brain counterexample:** two independent copies of an empty SQLite custody DB each record the same valid receipt; both return accepted. **Explicit passing rollback counterexample:** restoring a snapshot from before recording the nonce allows that nonce to be recorded again. These demonstrate that a local SQLite store cannot establish fleet-wide replay resistance.

## What remains hard NO-GO

1. **Receiver trust:** receiver-generated ephemeral keys are not operator-enrolled, pinned from an independent trusted channel, backed by durable independent custody, rotated or revoked. This branch models pre-pinning in the validator; it does not establish the producer-to-verifier enrollment protocol.
2. **Distributed epoch/nonce authority:** independently copied ledgers and rollback can each accept the same receipt. Need one globally authoritative fencing epoch and trusted monotonic/consensus-backed consumed-nonce custody with isolation from worker/API and verified recovery when storage is unavailable. Local SQLite is NOT a substitute.
3. **Truth & graph failover:** receiver must bind the exact server instance/start generation, not merely recycled Neo4j transaction numeric IDs. Need independent physical completion proof across server restart/failover, long observer outage and abort/cleanup, separate keys, and durable audit.
4. **Authenticated multiple hosts/API workers:** 1/3/5/10 physical reads with real ingress/proxy, actual Neo4j server transaction IDs, Redis restart/network blackhole and WAN partition. Need strict rate/in-flight caps, p95/p99 and rollback. This research uses isolated CPU/file fixtures only.
5. **Trusted ingress #149, role/PII access, historic .env exposure and release approvals** remain separate holds.

**Outcome: valid evidence can now be recorded once *within one surviving local SQLite store* using an independently pinned public key, without creating release authority. Distributed durability remains falsified rather than claimed. Keep #148 OPEN and PR DRAFT.**
