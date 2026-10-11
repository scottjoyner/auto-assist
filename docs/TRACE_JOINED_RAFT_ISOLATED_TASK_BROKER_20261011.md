# AssistX B03 joined Raft→isolated graph broker — physical feasibility checkpoint
**October 11, 2026 · Draft research only · Live/production release remains NO-GO**

## Why this matters

Earlier physical work established true three-host etcd Raft and a separately observed real Neo4j Bolt transaction (#260), a scoped policy-writer process (#264), research HTTP idempotency (#275), and physical worker/network isolation (#295/#296). However they were different subprobes. **This experiment executes an actual Raft CAS before a real Neo4j read inside the same request through an isolated broker.** It is the first joined network-isolated graph-read experiment, but **NOT** a three-host failure-domain or independent closure proof.

### Actual physical result

On **x1-370** a disposable research fixture used `neo4j:5.26-enterprise`, a worker-only Python 3.12 container, a dual-interface graph/worker HTTP broker **additionally connected to a separate Raft-only bridge**, and **three real etcd v3.6.14 voter containers all running on the same physical host**. All three bridges were `--internal`; there were no published host ports, persistent graph volumes, production credentials or links to the existing AssistX and Neo4j containers.

**PASS, 62.77 seconds, exit 0** on source SHA `2c918ac661cf2b5893ddb9d685934e87c7693133`. The same request executed:
1. Broker validates fixture Bearer identity and required `Idempotency-Key`.
2. Broker validates the **fixed reviewed Task plan** and exact task ID.
3. Broker derives the stable operation from the pinned synthetic principal and idempotency key plus canonical plan/parameters.
4. Broker reads a pinned real etcd cluster/genesis/owner/term, executes authoritative Raft CAS to reserve capacity **before** sending Neo4j HTTP transaction.
5. Broker performs the approved read with a Neo4j user granted ACCESS and MATCH on the selected Task fields; the same user's `CREATE` is denied at the Neo4j server.
6. Exact key replay → **409**, changed plan with reused key → **409**, missing bearer → **401**, missing key → **422**, fourth distinct operation at capacity=3 → **503** with graph admission rejected.
7. Worker TCP to literal Neo4j Bolt IP, Neo4j HTTP IP and etcd-voter IP → **DENIED**; broker read succeeds.
8. Independent read of authoritative Raft document confirms **three pending** attempts and **zero closures**. HTTP success **does not release physical capacity**. All disposable resources removed.

[Structured physical evidence](../research/evidence/assistx-disposable-joined-raft-broker-20261011.json).

### Final majority-loss physical negative — PASS

A second complete, fresh disposable run (**62.56 seconds, exit 0**) repeated the accepted Task reads and authoritative snapshot (`3 pending, 0 closed`). **Then the harness explicitly stopped two of its own three etcd voter containers**, leaving one Raft minority member, and sent a fresh authenticated request through the **same isolated worker → broker** path. The broker returned **HTTP 503 `ADMISSION_UNCERTAIN`**, rather than executing a new graph read without majority confirmation. This is a real Raft protocol minority, but **all voters share the same physical x1-370 host**, so it does not establish independence from correlated power/storage failure. No production etcd service was touched and scoped fixtures were removed.

The original successful run (62.77 seconds) remains recorded. The additional fail-closed quorum test is in `tests/trace_disposable_joined_quorum_loss.py` and is launched only with the same two opt-in environment flags.

### Implementation and test surface

- `tests/trace_disposable_task_broker.py`: opt-in `JoinedRaftAdmission`; no worker-supplied owner/term/permit; pinned genesis/cluster, CAS *before* graph; fail-closed on uncertain quorum, and typed safe duplicate responses.
- `tests/trace_disposable_raft_http.py`: standard-library JSON gateway to a **disposable, unauthenticated HTTP etcd cluster on a private internal Docker bridge only**. Deliberately not a production transport.
- `tests/trace_etcd_quorum_fence_research.py`: signature-verifier imports lazy-loaded so admission-only broker can reuse original exact CAS implementation without installing third-party packages. Signature verification for witness/term takeover remains in the original code path; no bypass introduced.
- `tests/probe_trace_isolated_task_broker.py`: unchanged legacy no-Raft mode plus separately opted-in joined 3-voter mode, all resources temporary and scoped.
- `tests/trace_disposable_joined_bootstrap.py` and `tests/trace_disposable_joined_raft_worker.py`: exact three-voter bootstrap and physically isolated worker replay, capacity, reachability cases.
- `tests/test_trace_joined_disposable_raft_broker.py`: seven negative/guard tests including fake-KV exact ID collision, stale owner, quorum outage and reservation release omission.

### Explicit B03 blockers remaining

1. **Three different physical Raft voters, not three local containers.** The true x1-370/xwing/destroyer cluster from PR #260 must be joined to this *actual broker process* with scoped per-service mTLS, real etcd RBAC and independent failure controls. Three local containers sharing power/storage prove Raft protocol but not autonomous recovery.
2. **Authenticated writer isolation.** Here etcd HTTP is plaintext on an `--internal` network with no RBAC. A compromised dual-homed broker could directly write its scoped authority. PR #264's separate Unix policy writer (distinct UID still unproven) must be integrated without giving the worker a raw KV credential.
3. **Independent transaction witness and physical closure.** No graph txid/instance generation was bound, no independently verified signer checked completion, and no release was performed. **Three occupied slots remain**; do not infer a completed HTTP read equals physical query completion. Implement graph member/generation proof, signer custody, and exact witness receipt before granting release.
4. **Neo4j server-side stale term / bypass proof.** The server only enforces the disposable reader role; it does not independently require a signed Raft permit. A privileged broker could bypass its own CAS logic. The actual production API/worker continue to reach Neo4j directly (#294).
5. **Full Task/graph caller compatibility.** Source census (#301) shows 72 Neo4jClient methods, 149 potential method calls, and 194 direct driver-session call sites outside client; this broker only reads fixed Task projections.
6. **Production identity/operational acceptance.** Verified tailnet proxy identity, distinct OS/service secrets, signed immutable custody, durable replay history, power/restore/NOSPACE, real 1/3/5/10 user fairness, p95/p99, staged rollback and operator signoff not done.

### Conditional next acceptance

Join exactly one isolated broker read to the true three-host scoped RBAC/IPC policy writer, bind that *same physical Neo4j transaction* to an independent signer that physically confirms closure, and preserve the three-container network-bypass denial from the worker. Test quorum loss **while the Neo4j transaction is still active**, then confirm no duplicate or release until independent witness. Only then consider B03 a candidate for full physical acceptance.

The result does **not** close B01, B02, B03, B04–B11 or issue #148. Keep PR draft; production graph admission and automatic failover remain OFF.

## Reproduce on authorized disposable host

```bash
ASSISTX_DISPOSABLE_GRAPH_BROKER=1 \
ASSISTX_DISPOSABLE_RAFT_IN_BROKER=1 \
python3 tests/probe_trace_isolated_task_broker.py
```

No production service, Neo4j data, real account, NAS or Tailnet configuration is accessed. The test consumes temporary Docker resources with no host-published ports and removes them in `finally`.
