# AssistX B06 — Same-request held Bolt transaction, independently observed

**October 11, 2026 · Draft research stacked on [PR #312](https://github.com/scottjoyner/auto-assist/pull/312) · Production NO-GO**

## New physical acceptance

The previously separate experiments are joined **for the first time at the original Bolt transaction**. An authenticated, fixed-plan worker request enters the **isolated graph broker**, which obtains an exact original reservation from a separate policy writer backed by real **three-host x1-370/xwing/destroyer etcd Raft mTLS + exact-key RBAC**, **before the same broker opens an explicit Neo4j Bolt transaction**. While the broker holds the transaction open, a separate observer-only Neo4j user on a graph-only internal bridge detects exactly one original `neo4j-transaction-N` tagged with the broker's seven exact metadata fields, including operation, attempt, reservation, original epoch, term and fixed plan.

The observer is a separately provisioned database user with `SHOW TRANSACTION (fixture_reader)` limited to the fixture read account, and an explicit negative `CREATE` test. It has no writer/worker bridge, etcd credentials, broker RPC token, graph-write privilege, signing key or Raft release command. The broker's Bolt read account lacks CREATE permission. **Only the broker can touch its held transaction**; the worker provides no Cypher or original transaction metadata. After the separate observer sees the exact active transaction, the coordinator signals this test-only broker to finish the read. The observer then makes two ordered original-transaction absence observations under the strict identity contract from #312; the fourth new request is denied because three global Raft reservations remain occupied. A real two-voter physical majority-loss negative is then preserved by the parent fixture.

### Physical result

**PASS (exit 0)**, full disposable three-host research harness **105.37 seconds**, joined broker section **77.57 seconds**, original tested source head `3b3a2e2907eae3a5cac4406b70cfe34fb5c0d12d`. Existing unit tests and metadata contracts plus the new same-request code were tested against **Neo4j Enterprise 5.26, three real remote etcd voters**, and separate worker/broker/writer/observer containers. Every graph/worker/broker bridge remained disposable; no production Neo4j or AssistX data was accessed. All test containers, certificates and networks were removed. [Machine-readable physical record](../research/evidence/assistx-b06-joined-held-bolt-observer-20261011.json).

Crucial result: **the original reservation did NOT leave Raft** when Bolt committed or the worker received HTTP 200. There were **3 pending, 0 completed** after observations, and a new operation was refused at capacity. We have proven **same-request active/absent visibility, not signed safe closure**.

### Repeatable manual opt-in

```bash
ASSISTX_THREE_HOST_RAFT_RESEARCH=1 \
ASSISTX_RAFT_RBAC_RESEARCH=1 \
ASSISTX_THREE_HOST_SPLIT_WRITER_RESEARCH=1 \
ASSISTX_DISPOSABLE_GRAPH_BROKER=1 \
ASSISTX_B06_HELD_BOLT_RESEARCH=1 \
python3 tests/probe_trace_etcd_three_hosts.py
```

New standalone research-only files under `tests/`:
- `trace_b06_broker_held_bolt.py`: exact fixed-plan explicit Bolt transaction from the broker, metadata attached with `session.begin_transaction(metadata=...)`, bounded hold and no capacity release on timeout.
- `trace_b06_observer_disposable.py`: separate read-only graph user, HTTP `SHOW TRANSACTIONS` with limited privileges, exact `capture_active` and `observe_absence_only` predicates. Never signs or releases.
- `trace_b06_worker_held_request.py` and `trace_b06_worker_capacity.py`: original third logical request and fourth capacity negative.
- `tests/test_trace_b06_joined_bolt_observer_guards.py`: fixed plan/metadata, Raft-before-Bolt, separate observer and no-release offline guards.

All hosted CI tests remain offline and **do not** start a database, containers, remote SSH sessions or live credentials. The Neo4j Python driver is copied from the approved local operator host into the broker's disposable container for the manual fixture; no packages are installed on production hosts by this patch.

## B06 gating truth

| Requirement | Result |
|---|---|
| Same actual broker request: global Raft CAS → explicit Bolt transaction | **Physical PASS** |
| Exact broker-authored transaction metadata visible while active | **Physical PASS** |
| Separate graph user can SHOW only fixture reader transactions | **Physical PASS for observed transaction** |
| Separate observer database write attempt | **Denied physically** |
| Unique active transaction and ordered absence observations | **Physical PASS** |
| Raft capacity held after Bolt commits | **Physical PASS** |
| Raft majority loss denies new broker admission | **Physical PASS** |
| Independently attested database member/boot generation | **NOT PROVEN**; fixture labels only |
| Separate durable signer/key custody | **NOT INTEGRATED** |
| Original txid/server/generation bound in Raft and independently signed closure | **NOT INTEGRATED** |
| Exact signed closure/tombstone/release after physical completion | **NOT INTEGRATED** |
| Production proxy identity, end-to-end mTLS broker→writer | **NOT ACCEPTED** |
| Stale, privileged direct Bolt clients fenced server-side | **NOT IMPLEMENTED** |
| Live API/worker direct Neo4j reachability removed | **FAIL B01** |

The explicit transaction uses a test fixture `graph_boot='unattested-fixture-boot'` string and `graph_member='disposable-neo4j'` label. Those are **not** an independently established server generation or authenticity proof. The bounded active transaction and observation-only returned values deliberately provide **no release or signing capability**. Loss of the observer leaves capacity **held**. A successful query response or two absent observations must never be substituted for trustworthy physical closure custody.

### Next narrow B06 gate

Provision separately protected graph member/boot identity from a trusted database authority, independently write-ahead-log the original `reservation→member+generation+txid` binding durably, require detached signer key custody and a separate writer-only *exact* signed closure path, then physically demonstrate signer crash, graph restart/transaction ID reuse, quorum loss, replay, lost ACK, and stale owner failures. Only then can completed reservations be tombstoned. Keep PR draft and issue #148 open, production graph admission and automatic failover OFF.
