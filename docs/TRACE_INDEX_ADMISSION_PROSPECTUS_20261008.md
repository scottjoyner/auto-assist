# Prospectus — Protect expensive global Trace History reads

**Date:** October 8, 2026 EDT. **Scope:** draft frontend/API chain #121 → #122; next independent review slice. **Status:** preregistered before coding/admission experiments.

## Existing measured evidence
The isolated Neo4j 5.26.30 85k-group synthetic benchmark already recorded 986,001–1,802,101 profile DB hits per count/page query, with small bounded concurrency tests. These are prior observations. The existing app uses route-level Redis sliding-window admission for POST dispatch/events/ask/intents, but GET /api/traces is not yet rate-limited. Existing rate-limit key extraction trusts X-Forwarded-For unconditionally; externally supplied forwarding values can influence any new per-client limit unless the source is trusted.

## Predeclared predictions / acceptance
1. Add a dedicated trace-index read admission policy (no change to other API routes): fixed per-peer limit (12 index calls / 60s) and fleet-wide shared aggregate cap (60 index calls / 60s), with one atomic Redis Lua operation updating both ZSET windows or neither.
2. For trace history only, use the socket peer address (not unverified X-Forwarded-For) for the per-peer key, SHA-256 of that peer to avoid including a raw address in Redis key names. Document that users behind one reverse proxy may share a cap; separately authorize any trusted-proxy configuration before live use.
3. On Redis unavailable/connection error, fail closed with 429 and Retry-After for expensive history index, not an unbounded graph scan. Leave existing general limiters fail-open as-is, out of scope.
4. Failed attempts must not consume slots or modify global counters. Redis state transitions must be atomic across the two keys. Non-index GETs including /api/traces/{id} remain unaffected.
5. Exercise deterministic peer/global thresholds, expiry/fresh windows, spoofed forwarding headers, exact path/method matching and Redis failure. No production Redis/Neo4j/service writes or restarts; tests use stubs or isolated disposable Redis.
6. No claim of complete user fairness or cross-host isolation beyond one Redis authority; proxy config, production auth, Redis failover and p95 SLA require separate acceptance.

## Rollout safety
This is a proposed protective policy in a new draft PR stacked after #122. Do not enable it in production without confirmed reverse-proxy source IP behavior, authenticated-user impact, shared Redis availability, rollback and operator approval. No real trace text or credentials used for testing.

## Pre-test security amendment: require authentication before consuming global quota

Review identified that the existing rate-limit middleware runs before the FastAPI route authentication dependency. Placing the shared Redis quota there would allow unauthenticated floods to consume 60 fleet-global slots and starve legitimate investigations. This is an unacceptable availability boundary. Before testing the final route integration, we amend the implementation to invoke the dedicated trace-index admission guard **inside the authenticated handler, after user dependency resolution but before constructing/opening Neo4j**. Existing global middleware and its dispatch/event/ask/intent policies stay unchanged.

Additional acceptance: unauthorized/invalid requests must not consume Redis slots or open the graph; only correctly authenticated, validated list requests do so. This does not grant new auth capabilities, and socket-peer NAT fairness still needs deployed reverse-proxy review.
