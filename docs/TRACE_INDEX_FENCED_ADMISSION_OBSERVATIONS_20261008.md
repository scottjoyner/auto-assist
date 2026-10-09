# OBS-RC1 — Fenced trace-index research observations and next acceptance

**Session date:** October 8, 2026 EDT / October 9, 2026 UTC. **Reference prospectus:** `docs/TRACE_INDEX_FENCED_ADMISSION_PROSPECTUS_20261008.md` (written before real Redis experiment). **Status:** limited single-node research; NOT WIRED to FastAPI, not production admission.

## Tested contract and lineage

- Workstream: OBJ #137, index query performance #123, in-flight fencing #148, proxy trust #149, conflicting admission drafts #146 and #147, integration draft #150.
- Pure module: `src/assistx/trace_index_fenced_research.py` provides three source-owned Redis Lua scripts: atomic combined rate/slot acquire, nonce-bound release, nonce-bound renewal; `Policy` and typed decision/lease records. No runtime imports call Redis; it is not installed into any route or environment switch.
- Limits are **hypothetical**: 12 principal/60s, 60 fleet/60s, 2 concurrent/principal, 3 concurrent/fleet, 15-second lease. A shared `{assistx-trace-index-v2}` Redis hash tag keeps all key operations in one Redis Cluster slot. HMAC-SHA256 keys require a receiver-owned secret at the explicit call site; never derive authority from unverified header values or raw IPs.
- Synthetic Lua canary: `scripts/trace_index_fenced_redis_canary.py` AST-extracts exactly the committed scripts and runs an explicitly approved, SHA256-pinned cached Redis 7 image. No image pulls, network, host ports, mounts or live data; read-only filesystem, 0.25 CPU, 96 MiB, 64 process cap. Redis uses a temporary internal UNIX socket and no persistence. Guard tests prevent accidental canary execution from general CI.

## Observations against actual Redis 7

- Image SHA: `sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99` (pre-cached, not fetched); no production Redis contacted.
- Fleet request-rate test: first **2 admitted**, 3rd denied with fleet-rate reason.
- Real competing admission: **16** simultaneous invocations with global active capacity **3** gave **3 admitted / 13 denied**; no race-induced 4th admission.
- Principal active slots: **1 permitted / 2nd denied**, and release permits a later request without treating denied requests as rate consumption.
- Exact nonce replay denied. Duplicate release returns false; release from a different principal key is denied; renewal of a valid lease is allowed; expiry-and-replacement then stale release cannot remove the newer slot.
- `UNIFIED_FENCED_REDIS_CANARY_PASS`, zero host ports or mounts. This demonstrates *one Redis process's* atomic Lua behavior only, not Redis Cluster/failover or multi-physical-node network reliability.
- Offline combined synthetic test suite also covers malformed Redis replies, bad identity/key custody, inconsistent policy, bad lease and release/renew errors; all fail closed. Complete Python/Node/browser results are recorded on the exact draft branch commit separately.

## Comparison to preregistered predictions

| Hypothesis | Observed | Disposition |
|---|---|---|
| Atomic combined rate and in-flight slot limit | Real Redis single-instance 3/16 admitted with 3 slots | SUPPORTED in isolation |
| Denials don't consume windows or leases | Subsequent request admitted after release, within remaining rate | SUPPORTED for fixture |
| Stale/duplicate release cannot free a newer request | Replay, wrong-key and expiry negative tests pass | SUPPORTED in isolation |
| Server clock and hash-tag prevent multi-node errors | Script uses Redis TIME and identical key hash tag | DESIGN ONLY; Redis Cluster not tested |
| Redis outage and malformed replies fail closed | Python adapter negative fixtures | SUPPORTED in mocks; actual outage/reconnect unknown |
| Protected query execution and cancellation | No route wiring / no Neo4j | UNPROVEN |

## Critical limitations and remaining blockers

1. **Lifetime/cancellation:** the 15-second TTL is not a cancellation mechanism. If a DB operation outlives the lease, later reads can be admitted despite an outstanding query. Before live integration require timer-bound 4s per-query behavior, renewal ownership, cancellation on renewal failure, and fail-safe handling of lost acknowledgments and crashed workers.
2. **Redis Cluster/restart:** current proof is a standalone local server. Verify same-slot Redis Cluster Lua EVAL, failover, replication lag, rollback/restart, partial loss and 1/3/5/10-client cross-host simulations without affecting production.
3. **Identity and secrets:** issue #149 must prove trusted proxy strips caller-provided identity headers, and establish operator scopes. Use a receiver-owned HMAC secret, key rotation and ACL tests. Do not infer identity from unverified source labels or XFF.
4. **Unified policy:** #146 per-principal all-GET and #147 index-only peer/global limits are **competing designs**, not production-ready cumulative limits. The draft #150 AST sentinel must remain blocking. This study offers a *single index* policy; detail/evidence read budgets remain a separate planned slice.
5. **Semantics:** choose 429 for authenticated true rate/slot exhaustion, 503 on lost shared quota-store/key custody; record Retry-After and unknown counts in the UI, avoid auto-retry storms.
6. **Representative query cost:** PR #122 includes isolated 5.26.30 85k synthetic plans with about 0.986–1.802 million DB hits and medians 181–413 ms. Sustained production-equivalent p95/p99, resource pressure, filter cardinality skew and authenticated browser remain **NO-GO**.
7. **General CI health:** issue #143 is independent; new synthetic suite cannot be used to waive existing unit/recovery-canary failures.

## Decision and next actionable slice

**Accept as research evidence only.** The next slice is an opt-in FastAPI staging adapter that obtains a single lease **after authenticated identity is verified and before `_neo()`**, ensures exactly-one release in finally, denies on Redis outage, and tests forced cancellation on expiry/revocation. It must explicitly replace, **not stack**, #146/#147 index controls. Before any live work, verify #149 trusted-header ingress and gain resource-budgeted operator approval.

No production API, Redis, Neo4j, NAS, provider accounts, quota enforcement, task dispatch or software deployment were changed.
