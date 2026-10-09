# Trace read GET quota prototype — research-only opt-in gate

**Date:** 2026-10-08 EDT. **Objective:** OBS-RC1 #137. **Parent:** strict trace GET auth #141. **Scope:** no live API, Redis, Neo4j or provider calls; no deployment.

## Predictions (before running new fixtures)

1. The three trace GET endpoints can resolve operator authentication **before** considering request budget, avoiding quota-client identity derived from an untrusted `X-Forwarded-For` header. Invalid credentials must yield 401 or 403 and 0 quota attempts/graph sessions.
2. The new budget must be `off` by default and never contact Redis in that mode. Unknown/typo modes must deny with 503 instead of silently permitting unmetered reads.
3. With mode `enforce` and synthetic Redis EVAL admission, a positive decision admits a GET; a denied shared quota returns 429 + `Retry-After`. An exception, missing HMAC key, malformed EVAL result or Redis outage must return 503 and never execute a graph query.
4. A single Redis EVAL script using Redis server `TIME`, ZSET membership and one unique nonce per admitted request makes each *script invocation* atomic and should avoid the two-pipeline read-before-write race in the generic limiter. This has **not** been checked under real Redis concurrency.
5. Redis keys use HMAC-SHA256 keyed by an independently provisioned operator secret. No raw operator name, password, header, correlation ID or trace payload goes into a key. Rotating the key changes the Redis namespace and resets accounting; rotation must therefore be operationally coordinated.

## Implementation

- `src/assistx/trace_read_budget.py` — pure, no-I/O-at-import quota adapter with one atomic Redis EVAL script. Redis import/client retrieval happens **only when opted in**. The adapter rejects malformed replies and any Redis error with `TraceReadBudgetUnavailable`.
- `src/assistx/swarm_routes.py` — `_trace_read_budget` depends on the previously hardened `_trace_read_auth` first; only GET index, detail and task/registry evidence use the opt-in quota dependency. Auth failures precede quota decisions. All other endpoints unchanged.
- Configuration defaults: `ASSISTX_TRACE_READ_BUDGET_MODE=off`; research-only `enforce` requires **both** a working shared Redis quota store and `ASSISTX_TRACE_READ_BUDGET_KEY_SECRET` of at least 16 characters. This secret must never be committed; the length check is not an authorization or strength audit. No environment has been changed by this PR.
- Research parameters: 45 admitted requests per authenticated operator per 60-second sliding window, across all three trace GET endpoints combined; scope/limit/reset must be reviewed for real workloads before activation. A mode typo returns 503.
- `tests/test_trace_read_budget.py` — fake Redis EVAL replies and negative cases for malformed decision tuples, outage, unknown principal, invalid window/limit, HMAC key custody and key rotation. These Python unit tests do not execute Lua in real Redis; the separate guarded canary does.
- `tests/test_trace_read_auth_boundary.py` — synthetic FastAPI route contract for auth-first, opt-in, 429, 503 and fail-closed behavior, including no fake Neo4j session on denied requests.
- `.github/workflows/observability-readonly.yml` — adds new Python fixtures to existing read-only query-contracts job.

## Observed local results

- x1-370 isolated checkout based on strict-auth PR #141; `PYTHONPATH=src python3 -m pytest -q tests/test_trace_read_budget.py tests/test_trace_read_auth_boundary.py tests/test_trace_outcome_filter.py tests/test_trace_context.py tests/test_trace_task_evidence.py tests/test_trace_bench_guard.py` returned **116 passed, 1 existing Starlette deprecation warning**.
- The dedicated tests use fake Redis and synthetic operator headers only, and no real cache, authentication session, provider calls, trace content, graph or NAS. No changes to process runtime configuration or services.
- The previous in-process browser suite and its synthetic HTTP 429 behavior do **not** by themselves demonstrate Redis quota enforcement, production auth or correct reverse-proxy configuration.

## Reproducible guarded Redis 7 isolation trial (after initial synthetic checks)

- Source script: `scripts/trace_read_budget_redis_canary.py`, which uses AST extraction of the **exact source-owned** `_ATOMIC_TRACE_BUDGET_LUA` string. It refuses execution unless the operator supplies both `--allow-isolated-docker-test` and the complete matching cached-image SHA256. No image pull is allowed; the launch argument is the **verified immutable image ID**, never the mutable tag, preventing a check-to-use retag race.
- Cached Redis image used on x1-370: `sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99` (`redis:7-alpine`, cached locally). Do not assume the tag is immutable on another host.
- Docker guard: `--rm --pull never --network none --ipc none --cpus 0.25 --memory 96m --pids-limit 64 --read-only --tmpfs /tmp:rw,nosuid,noexec,size=16m --cap-drop ALL --security-opt no-new-privileges --user 1000:1000`. Redis bound only to a temporary in-container UNIX socket; no mounts and no host ports. An additional `docker rm -f` cleanup runs on process exit/failure.
- Reproduce only on an authorized isolated test host by obtaining the local SHA with `docker image inspect redis:7-alpine --format '{{.Id}}'`, reviewing the guard arguments and calling `python3 scripts/trace_read_budget_redis_canary.py --allow-isolated-docker-test --expected-image-id <inspected-id>`.
- Observation: first **10/10 admitted**, 11th **denied** with retry 60 seconds; independent quota key admitted; **24 simultaneous client invocations admitted 10, denied 14**; sliding window admitted again after 1.3s expiration. `REDIS_LUA_CANARY_PASS`, exit 0. No production secrets or graph accessed.
- Guarded-runner tests: `tests/test_trace_read_budget_canary_guard.py` refuses unapproved execution and image mismatch and statically checks no host networking or mounts. **Combined local Python suite: 122/122 PASS** with one existing Starlette warning.
- Limits: single disposable Redis process with synthetic inputs; no real fleet-node contention, Redis failover, real proxy, authenticated staging session, persistence/restore, source quota, CPU p95 or production operating approval established.

## Release acceptance (NOT completed)

- [x] Single-node Redis 7 real Lua script execution in an isolated disposable Docker container without networking/ports/volumes; 24 concurrent callers and a short sliding-window reset checked. This **does not** establish cross-node, restart or fleet clock behavior.
- [ ] Independently test multi-node concurrency, Redis failover/restart and Lua EVAL ACL, longer clock/window/reset runs and operational quotas under an approved staging resource budget.
- [ ] Verify shared Redis topology/ACL and key-secret custody on an approved staging deployment; record reset/rotation and fail-closed policy. A 503 on Redis outage is an intentional availability tradeoff and requires operator approval.
- [ ] Measure real operator UI GET load and global filter/evidence query concurrency; review 45/minute per authenticated identity across many users behind a proxy. This is a conservative research default, not a validated production SLO.
- [ ] Verify deployed operator authentication, expired session, wrong scope, real 429 + `Retry-After`, Redis outage 503, deduplicated GET path accounting, and staging Neo4j 5.26 query p95/p99 under a documented resource budget.
- [ ] Prove that no untrusted forwarding header or source label can change quota identity; no raw usernames, secret material or correlation IDs appear in Redis keys/logs.
- [ ] Human authenticated browser/mobile/keyboard/screen-reader acceptance and rollback under objective #137. Keep Usage & burn endpoints staged until independently available and authenticated.

## Explicit decision

**RESEARCH ONLY.** Do not enable the `enforce` configuration, provision a live Redis quota key, or deploy this code before separate operator approval and the Redis concurrency/staging acceptance gates. The default `off` preserves current running behavior, which means the trace GET budget remains **unproven** in production. This prototype does not provide provider-credit admission, authoritative quota-source claims, verified fleet executor identity or full-fidelity historical custody.
