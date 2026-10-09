# Observations — source-index cancellation and soft lease counterexample

**Execution date:** October 8, 2026 EDT on x1-370. **Hypotheses frozen:** TRACE_INDEX_CANCELLATION_PROSPECTUS_20261008.md, with a controlled amendment written before the second read-only probe. **Authority:** disconnected research only; no operational activation.

## Live-code boundary and prior context

Draft auto-assist PR #154 implements Redis-time based exact-token in-flight lease admission for authenticated GET /api/traces. It improves on rolling-minute quotas but expires after 30 seconds even if the original worker's Neo4j query has not physically stopped. The trace index count and page queries individually use 4-second Neo4j Query timeouts. Neo4j's transaction timeout is a database execution control; it is **not by itself a distributed deadline covering transport stalls, client checkout, proxy waits or old workers after lease expiry**.

## Isolated normal-network Neo4j 5.26.30 experiment

A disposable Neo4j 5.26.30 Enterprise container, separate internal Docker bridge, **no published ports, no bind mounts, no production credentials/data**, one CPU, 2,200 MiB RAM and PIDs limited to 256 was used. Its only access was the guarded inspected private bridge address. There were **zero synthetic graph writes**: the queries calculated over generated ranges, without even a persistent fixture graph. Existing production neo4j, assistx-api and other services were not touched.

### First calculation (inconclusive), retained as separate record
A small 2,500,000-range arithmetic count completed at all three short Query timeout settings. This did **not** constitute a cancellation test. A poorly scoped SHOW TRANSACTIONS filter searched a constant present in its own statement and counted the inspector itself as one benchmark query, a **test defect corrected before the second pass**. Original private/synthetic-only report: TRACE_INDEX_CANCELLATION_LIGHT_PROBE_20261008.json.

### Amended second calculation (bounded 12,000 x 12,000 nested arithmetic)
The amended source statement was read-only: two small number ranges, a dependent modulo predicate, then a count. Neo4j returns only an aggregate number. The corrected transaction inspection counted statements starting with UNWIND, excluding the inspector itself.

| Requested Neo4j Query timeout | Complete Python-driver wall time | Outcome | Follow-up health |
| --- | ---: | --- | --- |
| 0.001 s | 158.52 ms | ClientError, code Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration | OK |
| 0.03 s | 568.14 ms | Completed normally | OK |
| 0.2 s | 475.55 ms | Completed normally | OK |

The post-run read-only SHOW TRANSACTIONS check returned **0 active synthetic UNWIND probes**. This is a useful normal-network termination observation but **not** evidence about unresolved network stalls. **Do not infer** the 30 ms and 200 ms query server elapsed time from the larger client wall times: the driver wall time includes connection and fetch overhead, and the timeout applies to server transaction execution, not necessarily the entire client call. The small sample does not characterize timeout consistency or p95 under contention.

### Independent Redis time negative control

The new test explicitly creates a soft lease with occupancy cap **1**, keeps one synthetic query logically active, advances the fake Redis clock **past its 30-second TTL**, and observes a successor token admitted while the old query is still logically active. **Physical active count becomes 2 while Redis reports occupancy 1**. Exact old-token release still cannot free the successor token. Thus a TTL-based occupancy cap of 3 can similarly be exceeded under the stated failure condition; no hard fence has been proven.

## Tests and code

- tests/probe_trace_index_cancellation_526.py: guarded one-off read-only probe; fixed staging container name, private internal network, no host bind mounts/ports, 1 CPU and 2,200 MiB memory bound, no production URI or credentials, up to 3 bounded short-timeout probes.
- tests/test_trace_index_cancellation.py: **13 new offline tests** including the old-worker-overrun counterexample and fail-closed staging-target validation (wrong image/name/network/ports/mount/auth/IP/not-running/missing).
- Prior test_trace_index_inflight.py, test_trace_index_admission.py and test_trace_outcome_filter.py were also rerun. **78 Python tests passed in 0.21s total**, including the 13 new tests, one existing third-party Starlette/TestClient deprecation warning. This is a research-only suite; no production connection.
- Machine-readable synthetic-only observations: TRACE_INDEX_CANCELLATION_LIGHT_PROBE_20261008.json and TRACE_INDEX_CANCELLATION_RESULT_20261008.json. These contain no private trace values.

**Cleanup verified:** the named disposable container, its anonymous volumes and dedicated internal Docker network were explicitly removed after the second probe. Existing production neo4j and assistx-api containers remained running.

## Decision and residual blockers

**New evidence does not clear physical concurrency.** Lease expiration after 30 seconds does not force a hung database transaction to stop; a successor may be admitted, and the physical cap can exceed the nominal Redis cap. The short Neo4j timeout probe verifies limited cooperative behavior only and has no simulated transport partition, worker crash, Redis failover or real authenticated API request.

Before activating PR #154 or claiming closure of issue #148, demonstrate a production-applicable cancellation/fencing architecture, including:
1. Stable scoped query/transaction identity and association with lease tokens; positive confirmation of cancellation from the database or a conservative way to retain capacity while the old query may exist.
2. Driver checkout/acquisition timeouts, per-query deadlines and a **whole-request wall deadline** integrated with worker cancellation and session teardown; test interrupted networks and delayed server ACKs. Python thread timeouts alone must not free a slot.
3. Explicit operator decision about orphaned work and Redis failover: do not treat unconfirmed termination or TTL expiry as physical capacity recovered.
4. True authenticated multiworker 1/3/5/10 API+Neo4j contention, safe reverse-proxy principal identity and outage/fairness tests; instrument actual admitted vs physically executing statements.
5. Continue independent full-fidelity audit retention (#117) and UI deployment/browser acceptance (#123).

No model/proxy/Neo4j/Redis/NAS/collector or production code was changed. **Draft and NO-GO remain appropriate.**
