# AssistX #148 — live three-voter Raft admission to Neo4j and independent closure

**2026-10-10 | Research-only | DRAFT stacked after #256 | Production admission/failover: NO-GO**

## What changed

This slice connects the already-validated three-host Raft authority from draft PR #256 to the guarded graph-entry controller from #251, against a *real, disposable* Neo4j 5.26 instance on x1-370. The actor starting the graph transaction gets a dynamically committed reservation from the same etcd v3.6.14 Raft group distributed across **x1-370, xwing and destroyer**, not from a mock or local SQLite reservation. The full existing leader-election/quorum-loss acceptance runs after the graph experiment in the same opt-in session.

**Files:** `tests/probe_trace_quorum_neo4j_physical.py` (new physical experiment), `tests/probe_trace_etcd_three_hosts.py` (additional opt-in hook), `tests/test_trace_quorum_neo4j_physical_guards.py` (offline safeguards), `.github/workflows/trace-graph-entry-research.yml` (offline tests/compile only), `research/evidence/assistx-quorum-neo4j-real-transaction-20261010.json` (sanitized result).

**Manual-only opt-in:** both `ASSISTX_THREE_HOST_RAFT_RESEARCH=1` and `ASSISTX_QUORUM_NEO4J_RESEARCH=1` are required. No production API entrypoint imports the probe or starts containers. No current fleet services, Neo4j volumes, PostgreSQL databases, Tailscale/firewall/systemd settings or public routes are changed.

## Physical evidence (x1-370 orchestrator, 2026-10-10)

The final hardened run returned exit 0 in **46.90 seconds**.

| Ordered physical check | Observed |
|---|---|
| Disposable mTLS three-voter etcd cluster with single cluster ID | PASS |
| Disposable Neo4j 5.26 on internal-only Docker network with no host published ports or DB binds | PASS |
| `QuorumPlanGrantAdapter` obtains Raft CAS admission *before* `ProtectedGraphEntry` starts Bolt | PASS |
| Real Bolt transaction with gateway-created `metaData` | PASS |
| Detached signer process observes exact Neo4j `SHOW TRANSACTIONS` match and verifies quorum reservation | PASS |
| Detached process stores exact server ID, fixture generation and tx ID via quorum CAS | PASS |
| Two voting hosts stopped while actual Neo4j transaction remains open | PASS, graph transaction still visible |
| Gateway attempts second query during quorum loss | DENIED; only one real Bolt graph call |
| Voting hosts restored; original grant remains occupied | PASS |
| Another query with cap=1 after quorum recovery | DENIED; no driver-success release |
| Operator-signed takeover while transaction unresolved | DENIED |
| Detached witness asked for premature closure | DENIED while tx active |
| Transaction completes; detached witness sees two successive absences | PASS |
| Detached Ed25519 signature submitted to quorum authority | Exact reservation released; replay rejected |
| Clean signed transition to owner term 2 after closure | PASS |
| Previous gateway owner/term attempts new Bolt entry | DENIED |
| Baseline real-leader election, two-voter majority and minority direct raw Txn | PASS |
| Scoped temp containers/cert directories/network cleanup | PASS, cross-host checked |

**Important:** this is an open transaction held in a privileged research host process; it is NOT a worker-network isolation proof. The separate #252 experiment demonstrated isolated worker IPC and no direct Bolt within its own Docker fixture. A Neo4j transaction can continue executing while its quorum is unavailable; consensus only prevents *new accepted requests through this gateway*. It does not retroactively stop or server-fence existing Bolt work.

## Adversarial negative — authenticated raw etcd writer bypasses operator signature

The same fixture uses mutual TLS but has **not enabled etcd keyspace RBAC**. To prove the consequence instead of inferring it, a separate research-only authority key was initialized with a known owner/term, and a client possessing the current etcd mTLS credential issued a **direct raw `/v3/kv/txn` compare-and-swap** with new owner `forged-raw-kv-writer`, term **99**. The write committed **without any operator Ed25519 approval**, and a subsequent authority read returned the forged state. This is **expected fail-open behavior** of the current research security boundary and a hard production blocker. The main live graph namespace was not modified by this attack experiment.

This result is distinct from the prior *minority-of-one* raw Txn negative: a lone etcd voter cannot commit without quorum, but an authenticated **majority-connected client with raw write authority** can bypass application policy. Mutual TLS verifies caller identity but does not itself limit which keys that caller can mutate.

The [etcd 3.6 RBAC guide](https://etcd.io/docs/v3.6/op-guide/authentication/rbac/) documents roles/user permissions; its [JSON gateway guidance](https://etcd.io/docs/v3.6/dev-guide/api_grpc_gateway/) warns that gRPC-gateway does not support TLS CN authentication, so **Bearer authorization tokens / proper client auth or a native gRPC adapter** are needed if our Python HTTP JSON clients move to server-side etcd RBAC. Do not assume mTLS certificate Common Name silently creates a scoped etcd role through the gateway.

## Validation

- Previous draft #256 suite: 90/90 local focused offline tests PASS.
- This draft adds six static physical integration guards, then one additional negative guard for the authenticated raw-KV policy bypass. The hosted research workflow compiles the physical probes but **never** starts SSH/Docker or uses real fleet credentials.
- Final exact-branch offline and hosted CI must be reverified at the head following evidence/doc commits; do not claim new tests green until observed.

## Remaining production gate

1. **Scoped policy writer and authenticated authority service:** enable and test etcd RBAC on disposable quorum; gateway worker identities may read status but cannot raw-write the owner/term/occupancy key. Implement a narrow separately authenticated authority service to perform allowed `admit`, `bind`, and verified `close` as atomic Raft CAS writes. Preserve operator signature and witness custody validation at the writer boundary. `EtcdTLS` uses the JSON gRPC gateway: mTLS CN alone does not provide RBAC identity through that proxy.
2. **Neo4j nonbypassable admission:** isolate all workers, host-network agents and alternate Bolt endpoints; exclusive gateway graph credential with minimally required Neo4j role. Even so, an existing Neo4j transaction can outlive leadership: reconcile it before turnover or use stronger transaction-server fencing.
3. **Independent custody:** move verifier key to a separate principal/host; immutable external append-only signed receipts with persisted replay ledger, group/membership/instance-generation constraints and DB-side release verification. Current detached signer process runs on x1-370 with local observation memory and no independent durable log.
4. **Real failure-domain and rollback acceptance:** three physically separate voters are on a shared tailnet and not validated as independent sites. Need node/switch/power partitions, restart/generation changes, copied-state adversarial cases, stale-effect rejection, orchestrator failure, key-loss recovery and operational rollback.
5. **API readiness:** authenticated 1/3/5/10 client p95/p99, reverse-proxy identity/NAT fairness, 429/503, trace-history lineage and privacy/data-retention reviews.

**Decision:** close only the bounded research hypothesis: a genuine Raft-backed reservation can precede a real Neo4j graph transaction, and a detached witness can gate capacity release in the disposable configuration. **Do NOT merge to production, wire live route, or enable automatic failover.** Keep issue #148 OPEN.

Stack: **#256 → #253 → #252 → #251 → #250 → #249**. No production authority granted.
