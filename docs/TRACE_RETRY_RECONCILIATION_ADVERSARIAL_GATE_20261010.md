# AssistX #148 — adversarial admission retry & failure-mode gate

**2026-10-10 · Research-only · stacked after draft #264 · No production admission or failover changes**

## Why this slice

The three-voter Raft and independent Neo4j witness acceptance (#256, #260, #264) demonstrates **safety under selected partitions**. It does not establish exactly-once execution. A particularly dangerous distributed failure is a **successful Raft CAS commit whose HTTP acknowledgement is lost**, leaving the caller unsure whether the physical reservation exists. Blind retries or a fresh operation ID can run duplicate effects or strand capacity. This is particularly relevant to etcd: documentation warns that a `NOSPACE` API error can accompany an internally applied write when a space quota is reached (https://etcd.io/docs/v3.5/op-guide/maintenance/).

## Implemented changes

- `tests/trace_etcd_quorum_fence_research.py`: a Raft CAS persisted `completed` tombstone ledger; exact stable operation IDs cannot be admitted again while pending **or after signed closure**. A read-only `reconcile_operation` checks the same pinned Raft cluster and returns `PENDING_EXECUTION_UNCERTAIN`, `CLOSED_NO_REEXECUTION` or `ABSENT_NOT_AN_EXECUTION_PERMIT`; it never yields a reusable execution token. Quorum loss fails closed. Signed physical closures and tombstone creation happen in one etcd CAS.
- `request_digest` binds the same stable operation ID to immutable canonical plan+parameter content. Reuse of an idempotency key with different plan or parameters yields `IDEMPOTENCY_KEY_PAYLOAD_CONFLICT`, both while pending and after closure. Legacy unbound test grants are still supported for previously stacked research, **not** accepted as a production policy.
- `tests/trace_graph_stable_identity_research.py` derives a stable operation name from a **verified** principal and caller-provided idempotency key, and the request fingerprint from the reviewed plan plus canonical parameters. This helper is **not connected to an actual authenticated API**, and it does not itself authenticate the principal. Request IDs should be supplied only after trusted identity validation, never from a spoofable header.
- `MAX_COMPLETED_OPERATIONS=128` is a deliberately small research bound. Admissions fail closed when the historical identity ledger fills, *without garbage-collecting tombstones*. Additional guard reserves enough ledger space for **all outstanding physical attempts to close**, avoiding a dangerous state where a legitimate signed closure cannot be recorded because more admissions filled the ledger. **This is a test safety invariant, not a viable unlimited production history design.**
- `tests/test_trace_admission_retry_reconciliation.py`: simulated `Txn` commit-success/ack-lost faults for both admission and closure, exact retry denial, concurrent duplicate IDs, payload conflicts, corrupted fingerprint, all-less-than-two-absent conditions, malformed identity, full-ledger and near-full release-capacity scenarios. Combined focused tests: **141/141 PASS** on x1-370 at commit `b5b3751e` (verify later exact PR head).
- `tests/probe_trace_etcd_ack_loss.py`: **actual disposable three-voter etcd 3.6.14 Raft** admission commit on x1-370/xwing/destroyer, then deliberately discards the success reply *at the research client adapter*. A second voter confirms the pending reservation; the exact request retry is denied, a changed payload with the same stable principal/request ID is rejected, and another operation is blocked at cap 1. Existing physical leader reelection, majority/minority and cleanup acceptance pass in the same opt-in run.
- `.github/workflows/trace-graph-entry-research.yml` extends offline-only CI and compiles the new manually gated physical probe.

### Last physical observation

**PASS, 23.38 seconds**, no production service or persistent data touched. The extra experiment is explicitly enabled via `ASSISTX_THREE_HOST_RAFT_RESEARCH=1 ASSISTX_RAFT_ACK_LOSS_RESEARCH=1`. It **does not** simulate a literal network partition at the ACK packet layer and does **not** run Neo4j in this particular probe; physical Raft→Neo4j acceptance is separately documented in parent PR #260. The existing original unresolved slot remains reserved throughout the consensus partition tests; nothing is released automatically.

### Full-stack physical regression

After the standalone retry acceptance, I executed **all four physical research slices together** on the disposable three-voter x1-370/xwing/destroyer Raft cluster:

`ASSISTX_THREE_HOST_RAFT_RESEARCH=1 ASSISTX_RAFT_ACK_LOSS_RESEARCH=1 ASSISTX_QUORUM_NEO4J_RESEARCH=1 ASSISTX_RAFT_RBAC_RESEARCH=1`

**PASS, exit code 0, 46.90 seconds**, running the exact code at `ef15617c`. The combined run included: actual Raft ack ambiguity and changed-payload denial; real Neo4j Bolt admission and a physical transaction that survived quorum loss; detached closure signer and exact receipt replay rejection; leader reelection and direct minority raw Txn denial; exact-key etcd RBAC reader/write isolation; and the independently spawned Unix policy-writer child committing an actual Raft admission and retaining capacity after death. This establishes *compositional compatibility within the disposable research fixture*, not a production fault-domain, OS-identity, or Neo4j server-fencing proof. The updated machine-readable evidence records this second complete run.

## Why this is still not sufficient for production

| Priority | Unclosed adversarial case | Concrete next acceptance |
|---|---|---|
| P0 | **API retries generate fresh operation IDs**, bypassing Raft's correct same-ID replay ledger | Authenticate principal at the actual HTTP ingress, require a stable idempotency key per logical user request, use a canonical payload digest and confirm transport/connection retries rejoin rather than execute again |
| P0 | **Gateway can access direct Bolt / stolen Neo4j credential** | Prove from every fleet node/sidecar and a compromised gateway process that only an isolated graph-entry identity and restricted server role can issue read/write operations; no alternate Bolt ports or Tailscale path |
| P0 | **Policy writer compromise** can raw-overwrite authority even with scoped etcd RBAC | Separate writer and gateway OS principal + credential issuance/custody; define writer authentication and enforce operator/witness checks inside a tiny service, inspect Linux namespaces, ports and procfs |
| P0 | **Neo4j cluster/instance restart or failover while tx ID disappears** | Pin server database generation and cluster identity, test stale IDs reused, elections/restarts, and independently observed physical closure; never treat two absent snapshots on the wrong member as release proof |
| P0 | **Witness lost before durable signed custody** | Externally anchored append-only receipt + independent verifier key + persisted replay registry; test crash between fsync, signed record, Raft CAS, and gateway return |
| P0 | **Storage crash, backend quota, NOSPACE / WAL restore** | Real etcd quota, compaction, defragmentation, leader/copy restore and crash-after-commit injections; reconcile exact operation against majority; no unsafe automatic term rollover |
| P1 | **Tombstone growth and identity epoch turnover** | Replace 128-entry single-document ledger with bounded/sharded independently durable operation index, atomic CAS and proof-preserving GC; demonstrate capacity/liveness without replay windows |
| P1 | **Floods, starvation, latency and tenant fairness** | Authorized 1/3/5/10 client load, rate controls, long-running read/cancel, p95/p99, memory/I/O budget, NAT identity, backpressure and 429/503 |
| P1 | **Trace leakage and lost audit lineage** | End-to-end trace IDs, PII-safe envelope, log correlation, encrypted/append-only provenance across gateway→Raft→Neo4j→witness→API, operational alerting |
| P1 | **Shared power/switch/failure domain, stale backup** | Physically independent site/fault-domain drill, network partition + correlated reboot, test restore-from-snapshot vs monotonic term, runbook and rollback |

## Acceptance interpretation

- **Safety property experimentally demonstrated:** with a preserved exact ID, the same quorum-committed reservation cannot be silently reused after lost ack, and a differently signed/changed payload for that ID is denied.
- **Liveness remains intentionally bounded:** a lost operation may remain unresolved, and after 128 historical closures the research ledger denies new work. Operator-verified archive/rollover design is mandatory before production.
- **No at-most-once end-to-end API claim:** upstream clients still need a trustworthy stable identity. Neo4j may keep running an old query while Raft is unavailable; consensus and idempotency do not physically terminate database effects.

**Disposition:** Narrow research GO for commit-ack ambiguity reconciliation; **production admission/failover NO-GO**. Keep issue #148 OPEN and the new PR DRAFT. Do not mutate existing fleet services.

**Documentation reference:** Neo4j `SHOW TRANSACTIONS` visibility is per server/connection scope, so absent status on one member is not a cluster-wide proof: https://neo4j.com/docs/operations-manual/current/database-internals/show-and-terminate-transactions/
