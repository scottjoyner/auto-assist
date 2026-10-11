# B02 & B03 release unblocking — graph-consumer inventory and joined broker/Raft research

**Date:** October 11, 2026  
**Context:** [Release report](https://github.com/scottjoyner/auto-assist/blob/research/assistx-task-policy-projection-parity-20261011/docs/TRACE_GRAPH_ADMISSION_RELEASE_READINESS_20261011.md) · [Issue #148](https://github.com/scottjoyner/auto-assist/issues/148)  
**Release decision:** Production graph admission and automatic failover **NO-GO**.

## B02: actual graph-consumer surface

An AST scan of `src/assistx` (excluding test subdirectories) produced **229 candidate call sites in 51 Python files**, from 187 scanned source files: 33 Neo4j/driver constructors, 132 candidate low-level query/transaction calls, 18 candidate read-method calls and 46 candidate write-method calls. **No AST parse errors**. This is an intentionally conservative *potential dependency inventory*, not a proof of 229 authenticated physical operations. Aliases, dynamic dispatch, alternate drivers and worker runtime reachability still need human review.

High-impact write candidates include `src/assistx/api.py` (14), `intent_orchestrator.py` (6), `neo4j_client.py` (6) and `fleet_executor.py` (4). There are direct graph use candidates in `routers/review.py` and in ingestion, workers, maintenance, projection and agent modules. Current `get_task` consumers are not synonymous with all these graph operations.

**Implementation:** `tests/trace_graph_consumer_inventory_research.py` scans AST without importing AssistX or opening any Neo4j connections, producing per-file/line/owner/operation records and a categorized Markdown report. **[Complete 229-site manifest](../research/evidence/assistx-graph-consumer-inventory-20261011.json)** records every candidate by source file, line, owner and operation for review. `tests/test_trace_graph_consumer_inventory_research.py` checks that real API task claim, heartbeat, completion, executor claim and review Task reads remain inventoried and **never marked safe**. CI can rerun this against any source revision.

**Priority buckets:**

1. **Protect first:** API claim/heartbeat/complete, fleet executor claim, recovery identity checks, review and critical queue decisions. No common replacement until method-level policy and field parity is proven.
2. **Explicit write registry:** direct `session.run()`, `Neo4jClient` write methods, ingestion and task creation; broker's current read-only role must **not** be broadened to accommodate them silently.
3. **Independently staged reads:** approved exact projections (e.g. Task.id/status, then explicit claim guard), correlation/status and review queries with request-specific identity and resource budgets.
4. **Dynamic aliases and unknowns:** remain fail-closed and cannot be inferred safe from the absence of a simple method name.

**Do not interpret a static candidate count as a completed dependency migration. B02 stays OPEN.**

## B03: explicit new joined-gate implementation

Previously a three-host Raft experiment, an authenticated Unix policy service, and an isolated read-only Task broker all passed in distinct experiments. This branch adds **a new optional physical path** to join them:

```
Synthetic worker (isolated worker-only bridge)
    -> authenticated fixture bearer / fixed approved plan
    -> broker on worker + graph bridges
    -> Unix policy writer socket (private read-only bind, no etcd key in broker)
    -> real three-voter etcd Raft CAS (x1-370/xwing/destroyer)
    -> approved Task read via broker-owned Neo4j reader credential
    -> result to worker; reservation remains occupied
```

`tests/trace_disposable_task_broker.py` now calls a bounded `admit_stable` over the Unix socket **before** performing any Neo4j query, in the opt-in Raft mode. The broker pins `genesis`, validates exact receipt shape, derives a stable operation identity from a fixed synthetic server principal and per-request idempotency key, and hashes an immutable approved plan and Task ID into `request_digest`. Reused request IDs or changed plan conflict are denied before graph reads. Missing, malformed or uncertain writer responses fail closed (503); no automatic direct Bolt fallback.

`tests/probe_trace_isolated_task_broker.py` creates a private disposable socket and server-side `PolicyEngine`, with only the broker container permitted the *narrow, private policy-socket mount*. The worker receives no socket, writer credential or graph network. The writer's host-side peer UID is measured using an ephemeral, networkless, rootless/container canary because assuming container UID 0 == host UID 0 was falsified on x1-370. **SO_PEERCRED numerical UID by itself does not uniquely authenticate a container; on rootless/userns setups many containers can map to 65534.** Distinct OS/service identities and a cryptographic broker-to-writer channel (B05) are still release blockers.

When Raft is enabled, the broker does **not** mark a successful HTTP read as physical closure. Its committed reservations remain `RESERVED` pending separate independent transaction witness and signed closure, conservatively reducing capacity. This proves admission ordering, not end-to-end transaction-lifecycle liveness.

`tests/probe_trace_etcd_three_hosts.py` enables this joined fixture only with all independent research opt-ins; the standard production code path remains untouched. `tests/test_trace_task_broker_raft_join_research.py` exercises real Unix IPC success/duplicate/error envelopes, same-ID changed-plan digests, unavailable writer, malformed grant rejection and source ordering.

### Final physically joined acceptance — PASS

On x1-370 the manual command, with **all three independent opt-ins** `ASSISTX_DISPOSABLE_GRAPH_BROKER=1 ASSISTX_THREE_HOST_RAFT_RESEARCH=1 ASSISTX_BROKER_RAFT_RESEARCH=1`, successfully exercised a temporary three-voter etcd v3.6.14 Raft group on x1-370/xwing/destroyer, a separate policy-writer service, an isolated dual-network broker and an actual disposable Neo4j 5.26 Enterprise Task read.

**PASS, 78.16 seconds, exit 0**, at physical source SHA `cc385e16803094f75ae11083454ce284814bfa8b`. Exact observations:

- Real quorum-committed CAS admission happens at the broker's server-side policy boundary **before** the broker issues the Neo4j HTTP transaction. The worker has no Bolt/graph HTTP path, even by literal IP.
- The Neo4j Task reader role can read approved minimal and claim-policy fields but is denied `CREATE` by the database.
- The same worker request ID is denied on replay; a changed plan with reused ID is denied. No second graph read is authorized from either duplicate.
- **Additional physical failure drill:** after the server-side policy writer was stopped, the still-running isolated broker returned a fail-closed 503 on a new task read. The actual three-host Raft reservations stayed occupied. Neither HTTP success nor policy-writer death releases capacity automatically.
- The same run retained Raft leader reelection, two-voter majority, exact stale owner/term denial, minority write denial, and restoration of the quorum. No production graph or services were modified. Scoped Docker fixtures were removed.

**Important boundaries:** the joined experiment does not include an external Neo4j transaction closure witness for the broker's Task reads; holding the slots is conservative, not a complete liveness solution. The temporary policy writer runs on the research host, and Docker user namespaces mapped its container root credential to host UID 65534. A numeric mapped UID is **not an independent service identity**; B05 and B06 remain separate NO-GO gates. The current running worker/API still directly reach live Neo4j (B01 FAIL). [Machine-readable physical summary](../research/evidence/assistx-b02-b03-joined-raft-broker-20261011.json).

### Explicit non-goals that remain blockers

- The graph broker prototype uses a fixture bearer + fixed subject, **not** verified production Tailnet identity or client streaming retries (B04).
- The policy writer is a host research thread with temporary etcd credentials, **not independently protected service identity/credential custody** (B05).
- The broker has no live graph term validation at Neo4j server and **no detached external closure signer wired to these Task reads** (B06). A successfully admitted read remains held, not silently freed.
- The test requires a private host-mounted Unix socket in the broker only. It is a disposable laboratory exception to the earlier no-host-bind rule, not an acceptable production secret mount configuration.
- The live x1-370 `assistx-worker` and `assistx-api` still have direct Neo4j TCP reachability and configured credential keys (B01), and all the additional caller candidates above need parity before a cutover (B02).
- Backend NOSPACE/recovery, independent site failure domains, 1/3/5/10 authenticated client load, trace-history physical query cap, browser/UI and operator rollback (B07–B11) remain unaccepted.

### Permissible next steps

1. Use inventory to assign owner+per-consumer migration receipts rather than indiscriminately wrapping `Neo4jClient`.
2. Validate the newly composed physical path against the disposable three-voter cluster. **Only report PASS when the run actually succeeds.**
3. Add independently witnessed actual Task-read transaction closure to the isolated broker and enforce non-replayable exact receipt; after that prove no direct worker Bolt route even during broker death and Raft minority loss.
4. In separately approved staging, create distinct service identities, deny workers direct graph credentials and paths, retain a tested rollback; only then revisit B01 network cutover.
5. Never merge research branches or enable automatic failover solely because the physical canary and CI are green.

**No production services, graph data, active credentials, Docker networks or Tailscale configuration were changed for this work.**
