# Observations — never-expiring trace-index slot quarantine
**Date:** 2026-10-08 EDT. **Prospectus:** `TRACE_QUARANTINE_PROSPECTUS_20261008.md`. **Status:** synthetic research experiment, not a production patch.

## Goal and prior finding
Auto-Assist #160 falsified the assumption that a Redis lease's 30s TTL physically stops a Neo4j query. A new request can be admitted after expiration despite the old query remaining active. The experiment tests a more conservative alternative: occupy capacity in a Redis hash indefinitely until exact-token release after separately verified completion.

## Implementation
- `src/assistx/trace_index_quarantine.py` is a **standalone, unreferenced** research module. It does not integrate with `swarm_routes.py` or modify `rate_limiter.py`/production runtime.
- Lua acquisition atomically checks `HLEN` against 1–16 allowed slots and inserts a random 32-hex-character token. Redis-server time is recorded only as metadata; no TTL, `EXPIRE`, `PEXPIRE`, or automatic stale-token removal exists.
- Lua release is exact-token `HDEL`. The Python wrapper additionally refuses release unless a caller asserts `remote_query_termination_verified=True`; this assertion is **not a signed, independently verified Neo4j cancellation receipt**, and must not be used as one.
- Failed Redis operations, malformed responses, invalid tokens or unavailable inspection deny/return unknown; no fail-open fallback exists.

## Predicted versus observed

| Predicted gate | Result |
| --- | --- |
| No automatic successor on simulated TTL expiry | **PASS** — at cap=1, simulated old query remains active after 30,001ms and after one week, successor denied |
| Atomic cap across clients | **PASS** — 35 concurrent synthetic admission attempts produced exactly three grants at cap=3 |
| Exact-token release and stale-token protection | **PASS** — wrong/stale tokens cannot remove successor |
| Crash leaves occupied slot | **PASS** — zero liveness recovery without external independent cancellation evidence |
| Redis errors/malformed replies deny | **PASS** — no admission on error/invalid responses |
| Redis loss/split-brain never exceeds physical cap | **FAIL / falsified** — clearing Redis state while old query remains active allows another token at cap=1 (two physical active; Redis count one) |
| Production cancellation proof | **NOT IMPLEMENTED** — no signed query completion, PID/transaction binding, transport deadline or multiworker API test |

**118 focused tests passed** using Python 3.12 on x1-370: new synthetic quarantine unit suite plus previously established cancellation, in-flight, rate admission and outcome-filtering suites. One third-party Starlette/TestClient deprecation warning. The suite includes **expected negative controls** and is *not* an activation acceptance.

## Real isolated Redis 7 probe
A separately created `redis:7-alpine` container named `assistx-quarantine-test-20261008` was launched with `--network none`, no published ports/bind mounts, 1 CPU, 128MiB memory and bounded PIDs. Redis disk snapshot and append-only persistence were explicitly disabled **only for this disconnected synthetic instance**, so state-loss risk is intentional. A strict `docker inspect` preflight in `tests/probe_trace_index_quarantine_redis.py` rejects unexpected container/network/image/memory/CPU/bind/port attributes before any synthetic command.

Actual Redis Lua results:
- Cap=1 admitted first 32-hex token, denied second.
- Redis reported key TTL **-1**: no automatic expiration.
- Replacing the stored created timestamp with zero did **not** release or permit another slot.
- Wrong-token release returned 0; exact-token release returned 1, permitting next token.
- **Negative control:** after deliberately deleting the test key, another token was admitted while the previous physical query was represented as still active. Physical fencing is impossible to prove from volatile Redis occupancy alone.
- Output explicitly recorded `activation_permitted=false` and `production_access=false`. No production Redis host/DB/trace source/Neo4j data or service was read or modified.

The disposable container was stopped and cleaned after the test. The cleanup check was issued for its exact fixture name; no operational Redis container was touched.

## Engineering interpretation
This approach eliminates **TTL-induced over-admission while one Redis instance retains its key** at the cost of indefinite slot loss on crash. That is a **fail-closed/liveness tradeoff** and not a complete hard fence. A Redis restart, failover replay gap, split brain, external deletion, or falsely asserted query-completion callback could all over-admit. There is still no trusted proof that a timed-out/abandoned Neo4j query ended before capacity was freed.

**NO-GO remains:** A production-grade version must bind admission to a durable authoritative generation and physical execution identity; require independently observed remote termination (or conservative quarantine until it can be established), tolerate Redis failover without forgetting live work, test 1/3/5/10 simultaneous authenticated API workers and transport failure, and obtain explicit operational rollout approval. Do not substitute a Redis TTL, a Python exception, or this research callback flag for physical cancellation.

## Files and limits
`tests/test_trace_index_quarantine.py`: unit/adversarial tests. `tests/probe_trace_index_quarantine_redis.py`: exact-target guard and real disconnected Lua probe. `src/assistx/trace_index_quarantine.py`: standalone research implementation. `docs/TRACE_QUARANTINE_RUNBOOK_20261008.md`: reproducibility. `docs/trace_quarantine_v1.tex`: arXiv-style manuscript **not compiled/submitted**. No production route/module wiring or Neo4j writes.

The research is stacked on draft #160 for visibility; do **not** merge it as implicit permission to activate any of draft PRs #147, #154 or #160. Main release issue #123 and physical fence blocker #148 remain open. Full historical trace retention (#117) remains independent.
