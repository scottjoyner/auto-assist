# AssistX OBS-RC1 — Real Neo4j 5.26 async cancellation experiment prospectus

**Date:** 2026-10-09 EDT. **Status:** research-only; experiment is NOT deploy/merge/production authorization.

## Question and falsifiable predictions

Can a lease watchdog *actually stop* an in-flight, read-only Neo4j 5.26.30 query when Redis declines renewal, rather than merely discard the result?

1. A dedicated Neo4j 5.26.30 Enterprise instance (immutable locally cached image, zero host ports, isolated Docker **internal** bridge, disposable tmpfs data and a resource cap) can execute synthetic read-only Cypher. The cached image's default read-only setting must be diagnosed honestly; any dynamic config change applies to the disposable server alone.
2. An async Python Neo4j 6.2 driver query with a 3s server-side transaction timeout will be observable in the isolated DB's `SHOW TRANSACTIONS` before cancellation and absent afterward. A direct `asyncio.Task.cancel()` test provides the control trial.
3. The staging research adapter from #169 can wrap a *cooperative* query callback that runs an async driver in the current thread while its watchdog works in another thread. On a synthetic injected Redis renewal rejection, the callback uses the running event loop's `call_soon_threadsafe(task.cancel)` to ask the actual async driver to cancel. The task then raises `asyncio.CancelledError` (a `BaseException`), which must be captured through the wrapper's `finally` lease release and converted to an unavailable result when lease authority was lost.
4. Acceptance requires: `SHOW TRANSACTIONS` sees the tagged synthetic query before cancellation; Redis fake records one positive acquire, >=1 rejected renew, then one exact-nonce release; driver task reports cancellation; the server no longer lists the query; no result is returned; the production API/Redis/Neo4j are never connected.
5. If the driver or the server does not show real cancellation, mark **unproven**, not pass. A query-timeout expiry without observed cancellation does not count.

## Safety, boundaries and metrics

- Neo4j version: 5.26.30 (`neo4j:5.26-enterprise` cached immutable image); data is ephemeral in `/data` tmpfs on a dedicated **internal-only** Docker network, no host ports and no production mounts, maximum 0.75 CPU/2 GiB.
- Driver image: cached immutable AssistX API Python image, Neo4j Python driver 6.2; no fleet credentials; synthetic principal and a fake in-process Redis response (`ACQUIRE=admit`, `RENEW=reject`, `RELEASE=ack`). This proves Redis lease event integration only in mocks; Redis Cluster/failover is not part of this experiment.
- Query: 12,000x12,000 lazy synthetic arithmetic cross product with one aggregate and a **3-second server-side timeout**, canceled early. No graph write, files, secrets, historical events, or provider calls.
- Server-side query tracking: `SHOW TRANSACTIONS` restricted to a unique synthetic tag before and after; only boolean counts and elapsed times are recorded.
- Stop and clean up: remove the disposable Neo4j container and internal network after the test, including on failure. Existing `assistx-api`, Redis and production `neo4j` remain untouched.

## Non-claims even if passing

A synchronous Neo4j Session does not have a public `cancel()` method. This experiment would justify exploring an **async-only** query implementation with a verifiable cancellation path, not wiring the existing synchronous `list_traces` endpoint as if it were cancellation-safe. Multi-node Redis leases, trusted-header ingress (#149), real staging 4s query budgets, backpressure and p95/p99 (#123), broad CI (#143), nontrimming custody (#117), and executor attestation (#125) remain open.
