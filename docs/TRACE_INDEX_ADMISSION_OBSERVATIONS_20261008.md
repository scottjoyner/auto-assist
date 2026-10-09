# Observations — authenticated heavy trace-index admission
**Date:** October 8, 2026 EDT. **Source prospectus:** TRACE_INDEX_ADMISSION_PROSPECTUS_20261008.md. **State:** research-only feature stacked on draft global filter PR #122; not deployed.

## Prior evidence and risk
The isolated Neo4j 5.26.30 benchmark in parent PR #122 observed 986,001–1,802,101 synthetic DB hits per global count/page query across 85,000 invented groups and 170,000 invented events. Sustained unbounded GET /api/traces requests could cause load despite the existing per-query four-second driver timeout. Existing API middleware limits only POST dispatch/paperclip/ask/intent requests, not the historical trace-index GET. The prior general rate limiter trusts unverified X-Forwarded-For and fails open on Redis outages.

## Design tested
- Standalone TraceIndexLimiter accepts fixed thresholds per socket peer **12 requests / 60 seconds** and shared Redis-fleet aggregate **60 requests / 60 seconds**.
- A single Redis Lua script uses Redis server TIME, removes expired peer/global ZSET timestamps, checks both counts, and either admits by inserting into both sets in the same Lua operation or denies without consuming any quota. The two Redis keys use a shared hash-tag for Redis Cluster slot compatibility.
- The socket peer is SHA-256 hashed in Redis keys; raw IP does not appear in the key. Untrusted X-Forwarded-For is ignored for this route. Proxy-shared peers remain a live user-fairness concern.
- Redis exception, malformed script result or invalid peer fails closed; an authenticated GET is denied with HTTP 429 plus bounded Retry-After.
- Crucial late pre-test amendment: the API middleware runs before authorization. A global quota there would let unauthenticated requests exhaust the fleet budget. The final implementation invokes _admit_trace_index **inside** the already-authenticated route handler and **before** _neo(), leaving unrelated rate-limit middleware unchanged. FastAPI validates query parameters before executing the handler, so malformed queries do not consume admission slots.
- UI shows query budget and numeric Retry-After, without auto-retry and without inventing zero-count results.

## Test evidence

| Acceptance | Observation |
| --- | --- |
| Python test suites (trace admission + global outcome + Neo4j 5.26/5.23 guards) | **62 passed**; one existing Starlette TestClient deprecation warning |
| Node VM UI interaction suite | **12 passed**; expanded from 11 |
| Chromium/Playwright synthetic browser | **7 passed**; expanded from 6, including 429/Retry-After/unknown metrics |
| Isolated actual Redis 7 Lua execution | Peer limit and global limit exact: accepted two p1 requests, denied its third, accepted one p2 request, then denied further peers at global count three; rejected requests left global cardinality unchanged |
| Unauthenticated FastAPI route request | HTTP 401/403; injected admission spy not called; graph not opened |
| Authenticated route request | Admission spy called once, then read-only query executed |
| Malformed route query | HTTP 422; admission spy not called, graph not opened |
| Real Redis unavailable | Trace limiter fails closed (synthetic RedisError), 60-second Retry-After |
| Concurrent simulated Redis atomic test | 180 attempts across 12 Python threads admitted exactly 60 (simulated atomic evaluator, NOT a distributed or production concurrency benchmark) |

The actual Redis test ran with **redis:7-alpine** in an ephemeral Docker container, network=none, no published ports/bind mounts, 0.5 CPU, 128 MiB memory, PID cap, no persistence. It was reached only by docker exec redis-cli; it was stopped and removed. The script fails its preflight unless the exact disposable test instance and isolation properties match. No existing AssistX Redis keys, production Neo4j, tools, agents or private trace histories were accessed.

## Limitations and NO-GO gates

1. **Rate is not concurrency.** Sixty calls may arrive in a burst, and each performs up to two costly read queries. This implementation bounds accepted *window volume*, not simultaneous in-flight queries, query memory or cross-region failover. Production admission should choose limits after a distinct concurrent-in-flight gate or a justified smaller burst budget.
2. **Reverse-proxy NAT.** Using socket peer prevents caller-controlled X-Forwarded-For spoofing, but may group many legitimate users behind one proxy/IP. Determine trusted proxy topology and authenticated user identity strategy with explicit operator review; do not trust arbitrary forwarded fields.
3. **Fail-closed outage.** Loss of Redis prevents index search rather than leaving Neo4j unguarded. Existing unrelated POST limiters retain their previous fail-open behavior, which is out of scope. Operator must approve outage/error semantics.
4. **Global Redis authority.** The atomic test proves single Redis Lua execution and matching keys, not active-active cross-instance split brain, Redis Cluster configuration or observed production p95.
5. **Production auth remains separate.** The FastAPI route test explicitly injects auth; the live app's previously observed 401 is not proof of an authenticated deployed browser acceptance. No production API service or middleware was restarted.
6. **No added execution or provenance authority.** The GET index remains only a read-only router-event investigation surface. It does not prove full historical tool-call retention, source identity, or authenticated agent execution; issues #117/#123 stay open.

## Decision
**Synthetic policy and actual isolated Redis Lua checks pass, deployment remains blocked.** Review the reverse-proxy identity, rate/concurrency budget, Redis availability and 429 UX with operators before a staged activation. Keep parent PR #121/#122, related provenance PRs, and this slice as drafts.
