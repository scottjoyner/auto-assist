# Operator runbook — AssistX Trace History in-flight admission research

**Date:** 2026-10-08 EDT. **Branch:** `feature/assistx-trace-inflight-20261008` stacked on rate-admission draft #147. **Status:** synthetic validation only; NO live service activation.

## Offline suites
```bash
cd /home/scott/git/wt-assistx-trace-inflight-20261008
PYTHONPATH=src python3 -m pytest -q \
  tests/test_trace_index_inflight.py \
  tests/test_trace_index_admission.py \
  tests/test_trace_outcome_filter.py
python3 -m py_compile src/assistx/rate_limiter.py \
  src/assistx/swarm_routes.py \
  tests/probe_trace_index_inflight_redis.py
```
Expected from the October 8 checkpoint: **65 tests passed**, one third-party TestClient deprecation warning.

## Disconnected real Redis Lua contract
Only run with explicit Docker authorization and confirmation that this exact container name does not already exist. The probe has its own preflight checking **exact disposable name, redis:7 image, network=none, zero published ports/bind mounts, read-only root, CPU/memory caps**, and refuses unsafe targets. It accesses Redis only through `docker exec`, never the fleet service.

```bash
docker run -d --name assistx-trace-inflight-redis-20261008 \
 --network none --cpus 0.5 --memory 96m --memory-swap 96m \
 --pids-limit 64 --read-only --tmpfs /data --tmpfs /tmp \
 --security-opt no-new-privileges \
 redis:7-alpine redis-server --save '' --appendonly no

python3 tests/probe_trace_index_inflight_redis.py

# Mandatory isolated test cleanup:
docker stop --time 5 assistx-trace-inflight-redis-20261008
docker rm -v assistx-trace-inflight-redis-20261008
```

**Verified:** synthetic 1/3/5/10 burst admissions at most 3, stale token release denial, Redis-owned lease expiry, and a composed 10-client same-peer workload with **2 accepted / 8 denied** when per-peer window capacity is 2. Results committed in `docs/trace_index_inflight_redis_results.json`. Any interruption must be followed by checking/removing *only* the named disposable container.

## Decision and authority
- Runtime scope is only the authenticated `GET /api/traces` index handler: acquire global occupancy lease, then apply existing post-auth rate quota, then open Neo4j, release token in `finally`. The detail GET and unrelated service routes remain unchanged.
- The 30-second lease is a recovery TTL **not a hard query cancellation or physical semaphore if DB calls outlive TTL**. No query interruption API or renewal heartbeat is implemented in this slice.
- Inflight denial must not spend the minute quota; quota denial must release a granted lease. A failed Redis admission denies before the graph is opened.
- The default shared global cap is 3 in-flight reads; the existing per-socket-peer 12/min and fleet-wide 60/min rate caps remain as in #147.
- Release cannot be approved until an owner validates real reverse-proxy authenticated identity, Redis outage semantics, actual driver cancellation within budget, simultaneous query load and rollback. Record release evidence in #148 and overall UI acceptance in #123.
- No production Redis/Neo4j access, model calls, NAS writes, source collector changes, trace-retention repairs or live service restarts are part of these test commands.

**References:** `TRACE_INDEX_INFLIGHT_PROSPECTUS_20261008.md`, `TRACE_INDEX_INFLIGHT_OBSERVATIONS_20261008.md`, [issue #148](https://github.com/scottjoyner/auto-assist/issues/148), [parent PR #147](https://github.com/scottjoyner/auto-assist/pull/147).
