# Exact-head real Redis 1/3/5/10 admission contention — research observation

Recorded October 9, 2026. **Research-only, not production authorization.**
Follow-on to [#148](https://github.com/scottjoyner/auto-assist/issues/148)
and [#200](https://github.com/scottjoyner/auto-assist/pull/200).

## Hypothesis and predeclared expected limits

With a real Redis 7 process executing the existing atomic trace-index Lua
`ACQUIRE` / `RELEASE` scripts, a *fresh disposable namespace*, a three-slot
fleet in-flight limit, and distinct synthetic principals, concurrent requests
of 1 / 3 / 5 / 10 should admit respectively 1 / 3 / 3 / 3 and deny the
remainder. All excess denials must be `fleet_inflight` (not rate quota or
Redis unavailability). An incorrect nonce must not release a capacity slot;
the exact owned nonce must release it. No slots may remain after a round.

| Concurrent workers | Expected admits | Expected denials |
|---:|---:|---:|
| 1 | 1 | 0 |
| 3 | 3 | 0 |
| 5 | 3 | 2 |
| 10 | 3 | 7 |

## Independent physical observation

- Clean x1-370 isolated worktree at source `423cb21eec6c9878db2282e73c751ae5791b05ef`.
- 7 focused offline guard tests passed.
- Explicit guarded Docker run of `run_trace_real_redis_concurrency_isolated.py`
  used a new `--internal` Docker network and freshly created Redis 7 Alpine,
  with non-persistent storage, no host-published ports, no production keys
  and a read-only synthetic Python client.
- Real `redis.Redis.eval` calls used the existing research Lua admission,
  with all worker threads released through a barrier. Workers with granted
  slots retained them while the client checked Redis `ZCARD` directly.
- The checker required all 1/3/5/10 expected grant/denial bands, rejection
  of a forged release nonce, exact-token release confirmation and zero
  physical Redis occupancy between rounds.
- Result: **`REAL_REDIS_1_3_5_10_CONCURRENCY_FENCE_PASS`**; the runner
  reported `CAPACITY=3; ALL_LEASES_RELEASED`.
- All test containers and the temporary network were cleaned by immutable
  recorded IDs. No real AssistX API or Neo4j query was started.
- Exact-head focused observability GitHub Actions initially passed all
  three jobs, including **264 Python tests** plus Node/Chromium, before
  adding this documentation. Revalidate after final head changes.

## Why this is not an end-to-end physical hard fence

This experiment demonstrates **atomic Redis slot admission**. It does not
measure the number of **physically running Neo4j queries**. A Redis lease
can expire or vanish due to loss of the Redis process while a driver-backed
query keeps running. The separate [#200](https://github.com/scottjoyner/auto-assist/pull/200)
single-client experiment shows real Redis restart can trigger async Neo4j
cancellation under that controlled setup; it does not prove the two results
compose across multiworker failover or disjoint nodes.

Remaining independently required:
- authenticated, operator-scoped API request admission from real ingress;
- synchronous production trace read path versus tested async research code;
- real 1/3/5/10 concurrent **Neo4j transaction** occupancy and deadlines;
- Redis multi-node failover, partition and split-brain with durable epoch
  ownership, authenticated cancellation receipts and conservative quarantine;
- browser/mobile client auth compatibility, #149 trusted-header ingress;
- #156 potentially exposed production credentials and #145 CI readiness;
- approved rollback and no cross-session/fleet workload disruption.

**Disposition:** keep PR draft and issue #148 OPEN; no production activation,
no live Redis/Neo4j or routing changes.
