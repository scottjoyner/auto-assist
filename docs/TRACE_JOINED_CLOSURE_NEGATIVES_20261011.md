# AssistX B06 — Joined three-host graph restart and closure-denial negatives

**October 11, 2026 · Draft research, stacked on PR #308 · Production NO-GO**

## What changed from the joined #308 admission result

[Draft PR #308](https://github.com/scottjoyner/auto-assist/pull/308) physically proved **isolated worker → graph broker → separate policy writer → real three-host mTLS/RBAC Raft CAS → disposable Neo4j read**, including a majority-loss denial. It did **not** bind the physical Neo4j transaction to an independent closure witness.

This slice tests a vital **negative**, rather than claiming B06 is implemented. A successful HTTP graph read, Neo4j outage or restart must **not** free a previously committed Raft slot. Without the original **server-observed transaction ID and independently attested server boot/generation**, even a syntactically plausible signed-closure claim must fail.

### Actual physical three-host run

**PASS (full script exit 0; joined section 88.34 seconds).** The fixture created new scoped real etcd voters on **x1-370 / xwing / destroyer** with fresh mTLS credentials and a dedicated etcd exact-key RBAC writer principal. The same broker request committed three real CAS reservations before its static Neo4j Task reads. After normal HTTP results, it observed **3 pending, 0 completed**.

Then, only in a research-only extra opt-in mode, it stopped the **unique disposable Neo4j 5.26 Enterprise container**, checked the authoritative quorum *from the separate policy writer* and found **3 pending / 0 completed**. It restarted **that same temporary container**, verified a change in Docker `State.StartedAt` plus successful Neo4j query readiness, then checked the Raft authority again: still **3 pending / 0 completed**. This `State.StartedAt` evidence confirms a disposable container restart, **not an independently attested Neo4j cluster-generation proof**.

While the same isolated broker/writer remained running, another physical step stopped the research voters on xwing and destroyer; a fresh worker request was rejected **503 ADMISSION_UNCERTAIN**, the exact voter containers were restored and three reservations remained. Separately, the real RBAC research administrative reader inspected the joined authority and invoked `close_with_witness` using a deliberately fabricated closure claim for every **unbound RESERVED** attempt. Each was refused with `NO_VERIFIED_PHYSICAL_BINDING`; none changed quorum state.

Physical results: [structured evidence](../research/evidence/assistx-b06-graph-restart-closure-negative-20261011.json). No live Neo4j, AssistX graph, production writer authority, user credentials, Tailscale ACLs, host routes or NAS data were modified.

### Reproduce only as disposable research

```bash
ASSISTX_THREE_HOST_RAFT_RESEARCH=1 \
ASSISTX_RAFT_RBAC_RESEARCH=1 \
ASSISTX_THREE_HOST_SPLIT_WRITER_RESEARCH=1 \
ASSISTX_DISPOSABLE_GRAPH_BROKER=1 \
ASSISTX_B06_GRAPH_RESTART_RESEARCH=1 \
python3 tests/probe_trace_etcd_three_hosts.py
```

The additional `ASSISTX_B06_GRAPH_RESTART_RESEARCH=1` is never enabled by hosted CI. The fixture has no host-published graph ports and removes its randomly named research networks, temporary certs, containers and etcd data in `finally`. Offline `tests/test_trace_joined_closure_negative.py` verifies the fake closure and scope guards.

## Critical unmet B06 proof obligations

1. **Real transaction binding in the same broker:** The current broker uses `POST /db/neo4j/tx/commit`, which executes and ends an implicit HTTP transaction in one response. It does **not** retain a long-lived transaction ID and its server-observed metadata in Raft. The successful response can never stand in for signed physical closure. Neo4j's [HTTP API documentation](https://neo4j.com/docs/http-api/current/transactions/) describes explicit transactions, but implementing one alone does not prove independent observed closure.
2. **Independently witnessed member and boot generation:** The original `server` and `generation` passed to prior witness subprobes were fixture-derived; a Docker `State.StartedAt` difference is not a server-signed, cluster-wide generation identity. Reject apparent absences across restarts, network partitions, leader changes, and possible transaction ID reuse without an independent trusted identity and restart journal.
3. **Signer/key custody:** The pre-existing detached research witness in `probe_trace_quorum_neo4j_physical.py` exercised observing an active Bolt transaction and denying closure until it disappeared, but that was a **different execution chain**. The new isolated broker has no witness service, signer-only private key, durable append-only signed records or separate service principal.
4. **Authorized release/replay:** `EtcdQuorumFence.bind` and `close_with_witness` must be integrated with the scoped policy service after independent graph observation; only an exact signed closure should atomically transition **STARTED → completed tombstone**, with lost-ACK reconciliation, duplicate and malformed receipts rejected. Preserve term, owner, graph member, boot generation and txID equality.
5. **Graph server-side stale-effect rejection:** A privileged direct Bolt client could still execute a query without relying on application admission. Existing live AssistX API/worker also directly reach Neo4j. Do not confuse a network-separated research read with server-side stale-term fencing.

**Release status:** B06 **negative fail-closed behavior PASS**; **signed closure custody and independent generation binding NOT IMPLEMENTED**. B01 live direct Bolt access FAIL, B02 consumer migration open, B04/05 production identity and privilege custody partial, B07–B11 separately pending. Keep issue #148 open, all research PRs draft, production graph admission and automatic failover **NO-GO**.

### Next smallest B06 slice

Introduce a separate opt-in *explicit* physical graph transaction in the broker (rather than one-shot HTTP commit), attach an immutable reservation/attempt metadata tuple generated by the broker, and make a detached **observer with separate graph credentials** observe that exact tuple while the transaction is **active**. Pin independently observed graph member/boot identity before allowing any closure assertion. First verify active→absent and restart/uncertain-state denials **without capacity release**. Only after independent durable signer custody and clean adversarial negative results should a signed receipt be permitted to invoke `close_with_witness`.
