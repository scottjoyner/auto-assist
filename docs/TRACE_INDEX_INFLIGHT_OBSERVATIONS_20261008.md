# Observations — cross-worker in-flight trace-index admission
**Date:** 2026-10-08 EDT. **Hypotheses:** `TRACE_INDEX_INFLIGHT_PROSPECTUS_20261008.md` frozen before implementation; composed-policy amendment recorded before its separate real Redis experiment.

## Implementation outcome
A new `TraceIndexLeases` in `src/assistx/rate_limiter.py` uses a Redis sorted set with one shared hash-tagged key, Redis `TIME`, a 30-second absolute expiry, and random UUID lease tokens. A Lua acquire removes expired tokens and atomically denies when three active tokens are already present. A separate Lua release removes **only the exact token**. On errors or malformed returns the class denies acquisition; release failure leaves the token until expiry. Neither script contains command/payload/trace IDs.

The authenticated `GET /api/traces` handler in `swarm_routes.py` obtains the global occupancy lease **after FastAPI authentication/parameter validation and before the already-existing rolling-minute quota**. It holds the lease through the two Neo4j read queries and `neo.close()`; finally releases the exact token. If capacity is exhausted, the endpoint emits an explicit HTTP 429 with Retry-After, and **does not consume the per-peer minute quota or open Neo4j**. If the minute quota denies, the occupancy lease is released without opening Neo4j. Other routes, provider selection, dispatcher, NAS and collector remain unchanged.

## Synthetic test results
- **65 Python tests passed** across `test_trace_index_inflight.py`, the previous `test_trace_index_admission.py`, and `test_trace_outcome_filter.py` (one unrelated FastAPI TestClient deprecation warning).
- New tests cover 1/3/5/10 fake concurrent clients; exact UUID ownership; repeated/stale release; Redis-clock TTL expiry; refused+malformed/unavailable Redis; release after normal graph result, graph exception, and rate-limit exception; no rate quota spent on occupancy denial; FastAPI authentication and validation occur before Redis; and improper caller tokens cannot be released.
- A **disconnected actual Redis 7-alpine** instance (no published ports, no host bind mounts, read-only root, ephemeral storage, 0.5 CPU/96 MiB) evaluated the real Lua scripts on synthetic UUIDs. Independent burst observations:

| Concurrent synthetic attempts | Leases admitted | Leases denied |
| --- | ---: | ---: |
| 1 | 1 | 0 |
| 3 | 3 | 0 |
| 5 | 3 | 2 |
| 10 | 3 | 7 |

An expired token could not free a newer holder; a worker with an expired 600ms synthetic lease allowed new admission through server-clock expiry, and a repeated release returned zero.

- A separately preregistered **composed** Redis test used 10 invented same-peer clients, global in-flight cap 3, minute quota 2 per peer and 5 global: **2 accepted, 8 denied** (7 occupancy refusals, 1 quota refusal). Quota-refused lease was released. Exact machine-readable synthetic results live in `trace_index_inflight_redis_results.json`.
- The disposable Redis container and its ephemeral volumes were stopped/removed after the tests. No production Redis credentials, Redis instance, Neo4j database, trace content, workers or running API service was accessed.

## Remaining **NO-GO** caveats
1. A TTL-based occupancy guard is **not hard cancellation**. A hung query that runs past 30 seconds may still be executing while Redis admits a successor, temporarily exceeding three *physical* in-flight queries. The existing 4-second *per-query* Neo4j timeouts normally reduce this risk, but do not prove a strict deadline for overall request execution, driver checkout, cancellation or failover.
2. Reverse-proxy identity is unchanged: the minute quota remains keyed by socket peer and may group many real users behind one proxy. Do not trust arbitrary Forwarded/X-Forwarded-For without a configured trust boundary and identity authorization.
3. Redis recovery/failover, multi-worker service restarts and actual GraphDriver cancellation under network stalls are not live tested. Redis outages deny index GET, not unrelated API routes; this behavior needs explicit operator acceptance.
4. Full production authenticated browser, 429/capacity and screen-reader acceptance remains open. Synthetic tests do not validate rate-limit UX against a deployed reverse proxy.
5. This lease does **not** authorize Neo4j writes, fleet tool execution, provider admission, or imply complete historical tool-call audit retention.

**Disposition:** a research-grade, token-fenced *soft occupancy lease* now complements the prior rate window. It improves safety, but **does not fully close issue #148** until physical cancellation/deadline and proxy-fairness tests, real 1/3/5/10 API+graph concurrency trials, and operator rollout/rollback are established. Neither parent #147 nor this child PR is activated.
