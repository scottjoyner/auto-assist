# OBS-RC1 — Staging-only fenced trace read adapter: retrospective and acceptance

**Session:** 2026-10-09 UTC, after unified Redis research draft #163. **Authority:** draft / synthetic-only; no FastAPI router connection or production configuration. **Parent objective:** #137; blockers #148 (in-flight authority), #149 (identity ingress), #143 (baseline CI).

## Prospectus and expected result before tests

1. After an **independently authenticated** operator identity is supplied, the adapter should acquire one atomic Redis rate+lease decision, only then call a **read-only**, cooperative query callback.
2. Quota exhaustion should make no query and be rendered as 429+Retry-After in an eventual route adapter; Redis failure/missing ownership should be 503. A synthetic positive decision is not authorization for fleet dispatch or provider-token quota.
3. Every normal or exceptional query completion should attempt one exact-nonce release; an unconfirmed release must suppress a successful result.
4. A watchdog should renew a live lease, signal a cancellation event and invoke a mandatory cancellation callback on renewal rejection/Redis error. If a query still returns despite losing authority, the adapter must never return that result.
5. If the watchdog itself hangs past its bounded join window, the adapter must fail closed; it cannot assume a lease is valid or claim cancellation succeeded.
6. This is **cooperative cancellation**, not a promise to stop an arbitrary synchronous Neo4j query. A Neo4j Python 6.2 Session/Transaction has no public `cancel()` method, and a lease TTL does not kill an executing transaction. Physical interruption must be proved with an asynchronous or driver-owned cancellation strategy on isolated staging before integration.

## Implementation: non-wired, explicit call surface

- `src/assistx/trace_fenced_staging_adapter.py`: pure Python staging coordinator around the source-owned `acquire()`, `renew()`, `release()` in `trace_index_fenced_research.py`. It imports no Redis transport and makes no runtime I/O on import. **No code in `swarm_routes.py` imports or calls it.**
- Requires a caller-supplied Redis client, independently authenticated principal, receiver-owned key, read-only cooperative `query(cancelled: threading.Event)` and `request_cancel()` callback. Incorrect/missing callback, configuration or secret fails closed.
- Starts only a short-lived renewal watchdog *after* successful admission. On any renewal failure, watchdog marks authority lost and calls cancellation callback; query cannot return an accepted response even if the callback ignores cancellation.
- Stops watchdog and attempts exact-nonce release on successful or failed query completion. Lost release acknowledgment or absent lease denies the result. Quota denial raises a typed distinct exception with `Retry-After`; Redis/admission/cleanup/renewal loss yields unavailable (503). HTTP status mapping is pure and **not registered into FastAPI**.
- Optional timing settings are only for deterministic subsecond synthetic tests. Invalid timing attempts lease release before denying.
- Explicitly **not supported:** physical cancellation of arbitrary blocking query, automatic HTTP integration, new RBAC principal, Redis Cluster failover, HMAC key rotation, source-provider credit admission, command execution or production mutating operations.

## Observed fixture results

- `tests/test_trace_fenced_staging_adapter.py`: **38/38 passing** against in-process fake Redis and fake query, covering normal release, rate/in-flight exhaustion, acquire failure, cleanup missing/timeout/malformed, query exceptions, successful renewal, renewal denial/outage, missed callback, ignored cancellation, timing failure, blocked renewal watchdog and no live route wiring.
- **Additional negative review after initial 25 tests:** input timing fields are now validated for numeric type, NaN, infinity and safe limits **before Redis acquisition**. A missing OS thread resource during watchdog startup now triggers an exact-nonce release attempt and fail-closed exception before entering the query, even when the release acknowledgment is unavailable. New tests cover malformed timing, denied thread creation and failed cleanup. The fixture initially expected invalid timing to acquire/release a slot; that assertion was updated to recognize safer pre-acquisition rejection, while lease-dependent timing still releases the slot.
- Fresh local combined synthetic Python suite: **212/212 pass** after these corrections; one existing Starlette deprecation warning. Dedicated CI must be rechecked against the exact *updated* commit.
- Existing upstream research suite must remain green on the same published head; dedicated read-only CI will include the new tests. Local fixture success is not a deployed API authentication or real Neo4j cancellation acceptance.
- Failure lineage: earlier draft #150 identified double-metering from sibling #147, and draft #163 proved Redis Lua atomic quotas/leases with 3 accepted / 13 denied in a disposable concurrent test; this adapter **reuses** #163's single policy, does not layer #146/#147.

## Remaining release gates (all OPEN)

| Gate | Required evidence before promotion |
|---|---|
| Identity boundary | #149 upstream proxy stripping/replacement, independently authenticated scoped operator, no XFF or caller-origin trusted-header authority |
| Read cancellation | Actual Neo4j 5.26 driver/transaction cancellation on lease loss or worker interruption; prove an unresponsive query is stopped within configured budget, not merely that its result is withheld |
| Lease ownership | Authenticated renewal and supersession, Redis lost-ack/replay/partition/failover, cross-worker crash, fencing after TTL, no orphan work |
| Rate design | One versioned default-off index policy; explicitly replace, never stack #146 per-operator and #147 per-peer/fleet. Detail/evidence cost managed separately |
| Resource budget | 85k-group Neo4j 5.26 p50/p95/p99 + plans/DB hits, bounded 4s query timeouts, query cancellation under load, operator-approved I/O budget |
| Security and rollout | Synthetic container plus real approved staging HTTP 401/403/429/503, GET-only data access, accessibility/touch, per-layer rollback and independent release approval |
| CI and historical custody | #143 broad baseline failures, #117 nontrimming history, #125 executor identity remain separate NO-GO |

**Disposition:** accept only as a repeatable staging research artifact. No server handler has been changed; the current production behavior and the Usage & burn staged navigation remain unchanged. The next engineering slice should use a transport whose cancellation can be physically witnessed, then add a separate default-disabled route integration behind explicit approval.
