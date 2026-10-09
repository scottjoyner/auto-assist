# #148 Receiver identity pinning and one-time evidence custody — 2026-10-09

**Research-only, no production admission and NO automatic release.** This child builds on draft [#218](https://github.com/scottjoyner/auto-assist/pull/218) and is source-only. No Docker containers, live graph, Redis, Tailscale, NAS, providers, devices, or credentials were modified in this slice.

## The critical distinction

Three independent statements must remain separate:

1. **Receiver identity:** The trusted Ed25519 public key must be approved and pinned **before** a receipt arrives. Merely including a public key alongside a valid signature is not a trust anchor.
2. **Evidence receipt custody:** A signature and exact graph/transaction/epoch/token/nonce binding can establish *who signed what*, and a surviving journal can record that particular claim only once.
3. **Physical capacity release:** Neither the signature nor the journal establishes fleet-wide truth, global ownership or safe release. The existing v1 `DurableTraceReadLedger.acknowledge_remote_closure` is **never called** from this work. No API/graph query admission code imports the new module.

The offline prototype `src/assistx/trace_receiver_replay_custody_research.py` implements only #1 and #2 in a **local disposable fixture** under `/tmp/assistx-receiver-replay-test-*/receiver-replay-test.sqlite`.

## Design and bounds

- Prevalidated `trusted_public_key` bytes (32) and a separately operator-approved key SHA-256 fingerprint are passed to the custody constructor; it rejects digest mismatch and never loads a public key from the receipt. **The prototype cannot prove the supplied approved digest actually originated outside the request**. Production needs a trusted configuration/PKI/KMS-provisioning channel and protected operator review.
- Explicit pinned UUIDv4 epoch, exact 64-char lowercase container ID, current schema and pre-existing SQLite file. Bootstrap only creates one **disposable research fixture**. Runtime opens with SQLite `mode=rw`, uses `BEGIN IMMEDIATE`, `synchronous=FULL`, a file-inode identity check, and mode permission restrictions. Missing, replaced, wrong-epoch, corrupt or locked journal fails closed rather than recreated.
- The **preissued expectation** table requires an explicit `register_expected(admission_token, query_ref, receiver_nonce)` transaction **before** `record` will accept a signed receipt. This is not a source of admission authority: only a future separately authorized admission service could legitimately issue these expectations. A receiver or request client must never be given the power to self-register arbitrary expected evidence. The registration is local, nonexpiring, unique by nonce/token, and transitions `prepared → consumed` atomically with evidence recording.
- The schema's **unique nonce, token and receipt digest** prevent replay/duplicate recording while the same authoritative local database survives. No TTL or auto-rearm. Restart retains previous recorded evidence. Separate local processes share a surviving file lock; this is not distributed consensus, NFS/CIFS locking or a fleet service.
- A caller can provide an independently grounded `external_minimum_count` checkpoint, refusing startup if the local journal has fewer records. Within a surviving process, a monotonic high-water count detects a local database rollback *in the same inode*. Neither check defeats a **stale or attacker-controlled checkpoint**, nor can a file snapshot prevent duplicate acceptance by another host.
- `record` returns `CustodyDecision(accepted=True, reason="observation-recorded-not-released")` after verification and durable journal commit; it **does not invoke any physical-query control or reservation release**.

## Native executed acceptance — x1-370

```sh
PYTHONPATH=src python3 -m pytest -q \
  tests/test_trace_receiver_replay_custody_research.py \
  tests/test_trace_receiver_evidence_research.py \
  tests/test_trace_durable_ledger_research.py \
  tests/test_trace_physical_probe_guardrails.py
```

**94 passed** on draft #223's original research worktree; child preissued-nonce tests are a separate acceptance gate. Included:

- 1/3/5/10 concurrent identical-receipt waves; at most one record succeeds with a surviving local journal
- independently preregistered nonce/token/query identity; unsigned/unregistered evidence, duplicate preregistration, mismatched token or query refuse before consumption
- four **independent spawned processes** contend for the same receipt, at most one winner
- survive close/reopen and reject duplicate receipt/token/nonce after restart
- wrong Ed25519 signer, incorrect externally pinned fingerprint, epoch, token, graph, query ref, transaction ID, nonce, malformed server result or incorrect signature all deny
- missing journal, held exclusive database lock, mismatched epoch, wrong path and changed-file inode never turn into implicit rearm
- a recorded receipt does not free a concurrently occupied Neo4j admission reservation
- **deliberate expected-negative controls:** two otherwise valid independent copies of an unconsumed SQLite journal **both record the same receipt**, and a copied-back old journal **accepts replay after restart when the externally supplied counter is also stale**. A correct independent high-water checkpoint denies that rollback. These passing tests prove why a local journal cannot be advertised as safe under fleet split-brain or global rollback.

All tests use synthetic identities, local `/tmp` fixtures and generated ephemeral test keys. No key material is committed. Hosted exact-head CI runs the four CPU-only suites, **not physical Neo4j/Docker**.

## Release NO-GO: the next needed proof

- A **separately operated, durable, trust-pinned** receiver key and protected lifecycle (operator key custody, revocation/rotation, delayed startup, key mismatch, graph-identity and version cutover), with an independently attested observation authority.
- A **single distributed durable ledger or consensus authority** that serializes actual admission, consumed receipt IDs and epoch changes across nodes, rejecting rollback/partition and indefinite remote uncertainty. Test 1/3/5/10 actual authenticated API workers on multiple hosts, not only local threads/processes.
- Real Neo4j transaction completion verification under graph instance restart, leader/replica changes, high latency and **management-plane blackout**. Actual Bolt blackhole and receiver-signed child-process observations in parent #218 do not imply these.
- Independently verified trusted ingress #149, operator-role/PII privacy scope, p95/p99 latency and an owner-approved live staging rollback procedure.
- **No auto-release, no trace paging activation, no staging/production merge, and do not close #148** on synthetic acceptance alone.

**Disposition: locally durable preissued evidence identity + replay protection demonstrated; globally safe replay protection falsified by copy/rollback controls. Registration is a synthetic local fixture, not an authenticated permission grant. This research remains draft.**
