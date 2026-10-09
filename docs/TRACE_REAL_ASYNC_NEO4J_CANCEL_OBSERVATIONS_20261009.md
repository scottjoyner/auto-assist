# OBS-RC1 — Real Neo4j 5.26.30 cancellation observations and custody

**Recorded:** 2026-10-09 UTC; node x1-370. **Status:** isolated research evidence, **NOT** production activation. Read the [prospectus](TRACE_REAL_ASYNC_NEO4J_CANCEL_PROSPECTUS_20261009.md) first; predictions were written before the lease-loss/real-database integration run.

## Exact isolated environment

- Server image `neo4j:5.26-enterprise`, immutable ID `sha256:96063a2c477d72aa71a019f9fe1c731fb865fd1e0eec8c94dffe8e02aeee629d`, self-reported Neo4j **5.26.30**; separate Docker network `assistx-neo526-internal-study-20261009` is **internal=true**, not published, not connected to running AssistX or production Neo4j.
- Disposable server `assistx-neo526-physical-cancel-20261009`: CPU cap **0.75**, memory **2 GiB**, PID cap 160, no published ports, `/data`, `/logs` and `/tmp` are `tmpfs`, no persistent host mounts or production credentials. Client image `sha256:9636c7b5776738cd23263a6a5ebdc79c3476e0aefc724ea5e57eef1e548d6a1c` with Neo4j Python driver **6.2.0**; client no ports, read-only container, CPU 0.4, memory 512 MiB and only read-only source/script mounts.
- **Startup finding:** With `--network none`, Neo4j exited code 3 with a boot configuration failure; a dedicated *internal-only* Docker bridge resolved network-interface startup. The cached image/config also starts with `server.databases.read_only=neo4j`, and adding an environment setting appended to that list instead of replacing it. Both the default `neo4j` database and newly created `syntheticcancel` were initially offline. **Only on the disposable test server**, an authorized Cypher admin call `CALL dbms.setConfigValue('server.databases.read_only', '')`, followed by `START DATABASE syntheticcancel WAIT`, brought the empty scratch database online. This dynamic setting is not persisted on restart. No production DB config was accessed or modified.

## Measurements and observation versus predictions

| Prediction | Measured observation | Disposition |
|---|---|---|
| Read-only synthetic query visible to the server before cancellation | `SHOW TRANSACTIONS` observed the unique synthetic Cypher tag | PASS |
| `asyncio.Task.cancel()` in Neo4j 6.2 async driver ends the active transaction | driver task cancelled and tagged transaction cleared from `SHOW TRANSACTIONS` | PASS; direct control 0.595 s overall |
| Simulated Redis lease renewal rejection invokes real transport cancellation | injected fake Redis `ACQUIRE`, `RENEW_DENIED`, `RELEASE_ACK`; cancellation callback called once | PASS |
| Rejected renewal never leaks query result | staging adapter rejected output with `FencedReadUnavailable` | PASS |
| Real transaction gone after integrated lease-loss cancellation | `SHOW TRANSACTIONS` cleared after async driver task cancellation | PASS; integrated 1.074 s overall with 1.0 s heartbeat |
| 3-second server timeout limits unmanaged query duration | query constructed with Neo4j `Query(..., timeout=3.0)` | DESIGN SAFETY BACKSTOP; expiry was *not* exercised as the success condition |
| Release remains correct for cancellation raising `BaseException` | adapter updated to capture `asyncio.CancelledError` through `finally` exact-nonce release and reject revoked authority; two dedicated tests | PASS synthetic + integrated |

- Direct test marker: `REAL_NEO4J_ASYNC_CANCELLATION_PASS`.
- Lease-loss integrated marker: `LEASE_LOSS_REAL_NEO4J_CANCELLATION_PASS`.
- Both tests used synthetic arithmetic `UNWIND range(1,12000) ... RETURN sum(x*y)`, a read-only streaming aggregate with a server-side 3s timeout. No graph event data, provider calls, command dispatch or retained file data were read/written.
- The integration uses **fake in-process Redis**, not an actual Redis outage or a cross-node lease authority. Prior #163 provided separate real single-Redis Lua tests; the two experiments must not be conflated into a production proof.
- The actual `assistx/__init__.py` initializer establishes unrelated runtime components (including a SQLite outbox) on import; the integrated isolated canary avoids those unrelated side effects by importing the two pure adapter/model source modules through a synthetic module package namespace. This is a test isolation control, **not** an application-runtime configuration change.

## Reproducible operator-gated test

- `scripts/trace_async_neo4j_cancel_canary.py`: control experiment with a real Neo4j async session and before/after transaction visibility.
- `scripts/trace_fenced_adapter_real_neo4j_canary.py`: **one fake Redis lease renewal rejection** triggers cancellation of the actual async task via `loop.call_soon_threadsafe(task.cancel)`, then verifies `SHOW TRANSACTIONS` is clear, result denied and owned release acknowledged.
- `scripts/run_trace_real_neo4j_cancel_isolated.py`: requires explicit `--approve-isolated-cancellation`, exact Git revision, exact immutable client/server image IDs, and exact, internal-only, single-container Docker test topology. Denies any unexpected host ports, non-ephemeral DB volumes, excessive server resources or live-service identity. The test runner never creates, starts, configures, restarts, or deletes the test DB; preparation of an approved disposable DB is intentionally separate.
- General pytest/CI runs only `tests/test_trace_real_neo4j_cancel_canary_guard.py`, never launches Neo4j, Docker or the costly synthetic query. Operators may invoke the real canary explicitly on a similarly isolated test host after independently preparing the disposable DB.

## Remaining NO-GO / next step

- **Production synchronous handler not converted:** `list_traces` uses the synchronous Neo4j driver. Do **not** assume the sync driver inherits async cancellation guarantees. A separate async/transaction-bounded read-query slice, with the correct 4s transaction budgets, must be implemented and tested before routing.
- **Redis and lease isolation:** #148 cross-node Redis/failover, authenticated lease renewal, worker crashes, lost-ack, query timeout-vs-TTL and counter reconciliation unproven. A fake `RENEW_DENIED` models an event; it is not physical network-failure evidence.
- **Trusted identity:** #149 must prove production ingress strips and reissues trusted-header identity only after independent authentication. No actor authority should be inferred from client-provided headers.
- **Query performance:** #123 still requires representative Neo4j 5.26 p95/p99 with filter-cardinality skew, rate/concurrency settings and resource caps. This test measured cancellation, **not** a production query benchmark.
- **Rollout and unrelated gates:** #143 broad repo CI, #117 nontrimming trace custody, #125 independent executor attestation and manual accessibility/rollback remain outstanding.

**Disposition:** physical *single-node* cancellation is now evidenced in isolation. Keep PRs draft, default-disabled and unwired; record exact published commit and dedicated GitHub CI results separately. No production API, Neo4j, Redis, NAS, provider or execution authority was changed by the tests.
