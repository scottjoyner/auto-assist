# OBS-RC1 — Single trace index admission with fenced in-flight leases

**Timestamp:** October 8, 2026 EDT / October 9 UTC. **State:** pre-experiment prospectus; research only, not wired into FastAPI or Redis production config.

## Motivation

Two existing draft designs (#146 and #147) silently double-admit GET /api/traces in a Git auto-merge: #146 per-principal (45/minute) all trace GETs, and #147 per-peer (12/minute) + fleet-wide (60/minute) index GETs. They have differing 429/503 outage behavior; neither independently caps concurrent in-flight database queries. An AST sentinel on draft #150 catches the collision; issue #148 still blocks deployment.

## Hypotheses and predictions (before running a real Redis Lua trial)

1. A single Redis EVAL that simultaneously verifies per-principal rate, fleet-wide rate, per-principal concurrent leases and fleet-wide concurrent leases will admit at most the lower of those four capacities, even when 10 independent CLI calls arrive in parallel.
2. Denied admissions must not consume either rate window or a lease slot. Once one admitted request releases, another should be allowed if its rate budgets remain unspent.
3. A lease must carry an unpredictable exact nonce; release and renewal require the matching nonce in both per-principal and fleet ZSETs. Duplicate releases and expiry-after-release must never free a newer request's slot. Renewal after expiry must be denied.
4. Redis server TIME and one hash-tagged key group must prevent cross-node clock divergence and Redis Cluster cross-slot EVAL errors **in principle**; the trial is one isolated Redis instance, not a Redis Cluster proof.
5. Malformed Redis decisions, key custody errors, unavailable Redis, and broken lease responses must not be turned into positive admission. Authenticated identity must be proved **before** invoking this module; the current trusted-header ingress proof remains blocked (#149).
6. No module import, CI pytest collection or disabled-by-default deployment may contact Redis or alter the running AssistX, worker, Neo4j, NAS or provider accounts. The unit tests will inject fake Redis objects. A separate, explicitly approved no-network, immutable-image disposable Redis canary will run only when requested.

## Bounded proposed contract

- **Scope:** index query only. Detail/evidence costs are a separate pending component. This is not a production route or a quota-admission authority.
- **Research policy defaults:** 12 per principal / 60 seconds, 60 fleet-wide / 60 seconds, 2 simultaneous per principal, 3 simultaneous fleet-wide, 15-second lease. Numbers are a hypothesis, not an accepted operational SLO.
- **Keys:** HMAC-SHA256 of authenticated principal with receiver-owned 32+ character key; Redis Cluster single hash tag shared by all four keys; random 128-bit nonce per request.
- **Admission:** a single EVAL on four ZSETs uses server TIME, prunes expired rate and lease entries, checks four limits, then increments all sets atomically on admit. Deny reason is explicit and retry bounded. Python interprets malformed responses as unavailable/fail closed.
- **Release / renew:** separate compare-and-release and compare-and-renew scripts require exact current nonce, in both ZSETs and not expired. Redis outage or malformed return makes release/renew **unconfirmed**; never claim success.
- **Important unresolved risks:** retrying a lost successful EVAL response may leave an unknown in-flight slot until TTL; 15-second expiry does **not** cancel an ongoing database read. Authenticated renewal, cancellation and enforced per-query timeout remain release gates. In-flight lease saturation returns 429 when true, and store unavailable must be 503 in a future route integration.

## Proof boundary / no-go

Research module is not imported from the FastAPI route or wired into any environment switch. Production running Redis keys, API and Neo4j will not be touched. A green test is not a multi-node failover acceptance, production trusted proxy identity, system-wide quota authority, or deployment approval.
