# Prospectus — Redis in-flight trace index leases

**Date:** 2026-10-08 EDT. **Decision status:** preregistered before implementing this experimental slice. Parent read-only admission PR #147 and blocker #148 are existing evidence.

## Question
Can the trace index enforce a fleet-wide cap on simultaneous expensive GET /api/traces reads, across API workers, without allowing stale/replayed releases to free another query's slot? The prior rolling-minute quota is not a concurrency fence.

## Predictions and falsification
1. One atomic Redis Lua acquire on a single hash-tagged global sorted set removes expired leases by Redis-server time and grants no more than **3 simultaneous active leases** fleet-wide. Each lease is a UUID token not derived from an IP or trace data; keys never expose raw peer IDs.
2. A release removes only the **exact token**; an unknown/stale release cannot decrement current capacity. Double release cannot free another lease. New slots are available after a completed release; a crash leaves occupancy only until expiry.
3. TTL **30 seconds**, deliberately greater than the nominal pair of 4-second Neo4j query timeouts. However, TTL is **not a hard cancellation fence**: a hung or slow query can outlive its lease and temporarily overlap a newly admitted request. The production acceptance must include timeout/cancellation and duration evidence; do not claim mathematically strict concurrency at arbitrary stalls.
4. A Redis outage or malformed Lua response fails closed, no graph access. If the per-peer rate limit denies after lease acquisition, the lease is released; rate-denied attempts should not hold a durable in-flight slot. A concurrent-denied request should not consume the rolling-window budget.
5. Existing FastAPI authentication and validation happen before lease acquisition. Only GET /api/traces (list) gains the guard; detail GET, dispatch, ask, and other endpoints remain unchanged.
6. Tests: mocked atomic Redis under 1/3/5/10 synthetic clients; TTL expiry and replay; release on normal return, exceptions and denied rate requests; redis unavailable; actual disposable Redis 7 Lua atomics if available. No production Redis/Neo4j or raw trace data.

## Scope / limits
Use a new isolated worktree based on draft #147. No actual service deployment, no operational Redis key or DNS connection, no NAS, Neo4j writes, provider or fleet command dispatch. Run only synthetic tests; use a disposable local Redis container with no published ports/bind mounts if needed. Record negative findings and immutable evidence. Keep drafts until trusted proxy identity, deployed auth browser, and cancellation/timeout acceptance.

## Amendment before combined real-Redis experiment
After the first standalone lease Lua run and before a new experiment, also test the **composed policy** in the same disconnected Redis instance: ten concurrent invented same-peer requests, global lease cap 3, per-peer rate cap 2 in a 60-second window, fleet rate cap 5. Predict at most two admitted combined requests and no retained lease on a rate-denied attempt. The result is a synthetic Lua-contract experiment, not live FastAPI/Neo4j end-to-end load.
