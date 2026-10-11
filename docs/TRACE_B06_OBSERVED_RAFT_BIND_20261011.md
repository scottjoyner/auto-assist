# AssistX B06 — Observation-bound original Bolt transaction persisted in three-host Raft

**October 11, 2026 · Draft research stacked on [PR #315](https://github.com/scottjoyner/auto-assist/pull/315) · Production NO-GO**

## New physical milestone

Earlier [PR #315](https://github.com/scottjoyner/auto-assist/pull/315) proved a single isolated worker request travels through **broker→separate policy writer→real x1-370/xwing/destroyer etcd mTLS/RBAC quorum→broker-owned held explicit Neo4j Bolt transaction**, where a different restricted Neo4j observer account captures the exact original `SHOW TRANSACTIONS` row while active. Its Raft reservation was still `RESERVED`, with no server tuple recorded.

This slice extends that **same physical request while its Bolt transaction is STILL ACTIVE**:

1. The graph-only observer saves its independently queried `transactionId`, database and exact original seven-field metadata. The disposable privileged test coordinator copies this evidence and the broker-authored target into the **separate writer container**. This transfer is **NOT independently authenticated trusted custody**; no private signing key is used.
2. The writer, which exclusively holds scoped exact-key etcd mTLS/RBAC credentials, compares the observer row and target *field by field*: the same unique transaction ID, `neo4j` database, exact metadata, original reservation token, `auth:<sha256>` operation, attempt, genesis, owner, term, read plan, and `RESERVED` state in the **authoritative quorum document**. The writer rejects a deliberately modified transaction ID **before writing**.
3. The writer invokes the original `EtcdQuorumFence.bind` CAS. An authoritative Raft reread confirms the original entry is now **STARTED** with its exact observed transaction ID, graph member and fixture boot label. A replay of the same binding is rejected, because the record is no longer `RESERVED`.
4. Only after this binding does the broker complete the original held Bolt read. The separate observer makes two ordered absence observations; these do **not** authorize signing or release.
5. Authoritative etcd has **3 pending/0 completed**, including the original STARTED item. The fourth read is still denied. A fresh request while two actual remote Raft voters are stopped is denied; voters recover and capacity remains occupied. A fake `close_with_witness` is rejected on both STARTED and RESERVED entries.

**Physical PASS, exit 0**. Three real Raft hosts: **x1-370, xwing, destroyer**. Disposable Neo4j 5.26 Enterprise. Full harness **106.43 seconds**, joined broker **79.85 seconds**, original physical source SHA `687b62f59d27402bd1fa8643221b61d9c56be2a5`. All scoped research containers and networks cleaned; no running production graph, route, credentials, Tailscale ACL, NAS data or AssistX process modified. [Machine-readable physical evidence](../research/evidence/assistx-b06-joined-observed-raft-bind-20261011.json).

### Implementation

- `tests/trace_b06_writer_bind_observed.py`: research-only writer-side identity contract and **exact-key Raft CAS STARTED binding**. Only supports `bad-txid`, `bind`, and `replay`; the script has no signer, closure receipt or release operation.
- `tests/probe_trace_isolated_task_broker.py`: extra explicit opt-in `ASSISTX_B06_RAFT_BIND_RESEARCH=1` (requires `ASSISTX_B06_HELD_BOLT_RESEARCH=1`); transports observer evidence in the privileged fixture, negative-bind before real bind, duplicate-denial after. No new worker graph or Raft network path.
- `tests/probe_trace_etcd_rbac_physical.py`: forged-closure negative now distinguishes unbound `RESERVED` from observed `STARTED`. Neither can be released without a valid matching signed witness receipt.
- `tests/test_trace_b06_writer_bind_observed.py`: source and fake-quorum negatives for changed txid, metadata, attempt, plan, generation, owner, duplicate, missing reservation and fake signature. Hosted research CI executes no SSH/Docker physical fixtures.

To repeat, use only approved disposable lab hosts and this **six-flag manual opt-in**:

```bash
ASSISTX_THREE_HOST_RAFT_RESEARCH=1 \
ASSISTX_RAFT_RBAC_RESEARCH=1 \
ASSISTX_THREE_HOST_SPLIT_WRITER_RESEARCH=1 \
ASSISTX_DISPOSABLE_GRAPH_BROKER=1 \
ASSISTX_B06_HELD_BOLT_RESEARCH=1 \
ASSISTX_B06_RAFT_BIND_RESEARCH=1 \
python3 tests/probe_trace_etcd_three_hosts.py
```

## Correct release interpretation

| B06 requirement | Result |
|---|---|
| Broker's original Raft reservation → actual held Bolt transaction | **Physical PASS** |
| Separate SHOW-TRANSACTIONS account sees exact original active row | **Physical PASS** |
| Writer stores original observed member/generation/txid as STARTED in Raft | **Physical PASS (identity labels still fixture-trusted)** |
| Mutated txid, changed reservation, duplicate binding denied | **PASS** |
| Same reservation remains occupied after transaction ends | **Physical PASS** |
| Real 3-host minority denies further admission | **Physical PASS** |
| Graph member/boot identity independently authenticated or server-signed | **NOT PROVEN** |
| Observer evidence digitally signed, independently held and durably journaled | **NOT PROVEN** |
| Privileged coordinator cannot forge both target and observer evidence | **NOT PROVEN** |
| Protected separate principals and broker→writer mTLS | **NOT PROVEN** |
| Signed external closure, replay-safe exact tombstone, release | **NOT IMPLEMENTED** |
| Neo4j server-enforced rejection of stale effects from direct privileged clients | **NOT IMPLEMENTED** |
| Production B01/B02 direct clients migrated and isolated | **NOT ACCEPTED** |

**Critical hazard:** A compromised privileged test coordinator could fabricate a *coherent* target and observer record. This implementation checks their internal consistency and the Raft reservation; it **does not** cryptographically establish that the graph server itself created that transaction, or that the purported boot generation is real. Accordingly, even the valid `STARTED` record must **never** authorize physical closure, reuse capacity, takeover or release.

### Next P0

Replace fixture-authored member/boot identity with an independently authenticated graph-instance witness and restart journal, ensure the original observed row is signed and durably retained by a separately protected principal (not passed as coordinator-controlled JSON), bind via an identity-authenticated, narrow writer API, and prove forged/coherently altered evidence and graph restart/txid reuse are rejected. Only then test *externally witnessed closure* and replay-safe Raft tombstones. Keep issue #148 open, research PR draft, production graph admission **NO-GO**, automatic failover **HARD NO-GO**.
