# AssistX RC1 — trace GET authentication boundary (isolated research)

**Date:** 2026-10-08 EDT. **Authority:** draft, synthetic only, not production activation. **Parent:** objective #137, integration draft #138.

## Prediction / preregistered hypothesis
1. Without the production `api.py` auth injection, the standalone `swarm_routes._default_auth` currently accepts a syntactically valid Basic username or returns `system` even when no credentials exist. For trace data this is an unsafe standalone mount and should fail closed, before `_neo()`.
2. Adding a trace-read-only strict dependency to three sensitive GET routes will not change the real `api.py` authentication mechanism; `api.py` already calls `set_auth_dependency(auth)` on its mounted router. A configured, nonempty operator identity must still be required.
3. A 129-character detail correlation ID will be rejected at FastAPI validation (422) before opening Neo4j, matching the evidence path's existing upper bound.
4. Denials from the injected auth handler, including 401 and 403, will preserve the original code without accessing Neo4j.
5. Existing global outcome and task-evidence functional tests will still pass after targeting the new dependency for isolated fixture overrides.

## What changed
- Add `swarm_routes._trace_read_auth`: fail 401 with WWW-Authenticate Basic if no operator auth injected, or if injected handler returns no nonempty principal; otherwise delegate to unchanged injected `api.auth`.
- Use that dependency on GET `/api/traces`, GET `/api/traces/{correlation_id}`, GET `/api/traces/{correlation_id}/evidence` only.
- Bound direct detail `correlation_id` at 1–128 characters (evidence path was already bounded); no new Neo4j calls.
- Update the explicit FastAPI fixture auth override in `tests/test_trace_outcome_filter.py`.
- Add `tests/test_trace_read_auth_boundary.py` with fake Neo4j and fake read projections, no real credentials, sessions, network, graph writes or service access.
- Extend `.github/workflows/observability-readonly.yml` query-contracts to require the new tests.

## Observation and local evidence
- x1-370; isolated `/tmp/assistx-rc1-auth-cA1eGwaL/repo` based on RC commit `d583e91bf033a3da6343e92603f0b36d3aad897a`.
- `PYTHONPATH=src python3 -m pytest -q tests/test_trace_read_auth_boundary.py tests/test_trace_outcome_filter.py tests/test_trace_context.py tests/test_trace_task_evidence.py tests/test_trace_bench_guard.py`: 79 tests passing, no failures; existing Starlette/TestClient deprecation warning.
- Denied anonymous or forged Basic requests to each GET open 0 fake Neo4j sessions. Empty/incorrect injected principals and injected 401/403 also open 0 sessions. Good identities can read synthetic fixtures and close the fake graph client.
- This proves **FastAPI router dependency behavior in isolation**, not the deployed service's Basic Auth config, HTTP reverse-proxy trust, real rate limits, screen reader behavior or production latency.

## Important separately identified rate-limit blocker
- In `src/assistx/api.py`, `RATE_LIMITED_ROUTES` currently enumerates only `POST /api/dispatch`, `POST /api/paperclip/events`, `POST /api/ask`, `POST /api/intents`. No trace index/detail/evidence GET appears. Do **not** tell the operator those GETs have a verified server-side request budget.
- Existing `_rate_limit_key` trusts a submitted `X-Forwarded-For` header without independently verifying trusted proxy source. Existing Redis-backed `RateLimiter.check` fails open when Redis raises an error. Simply copying this limiter to the trace endpoints would not establish a reliable, unspoofable quota boundary.
- The release gate is a distinct allowlisted GET limiter design with trusted client identity and explicit Redis-outage/fail-closed tests, plus real deployed middleware and query resource acceptance. **Do not activate rate-limiting changes as part of this auth-only draft.**
- The fallback for *other* swarm routes, including POST trace events, remains unchanged by this targeted patch and warrants its own router-wide audit before any standalone mount is treated as secure.

## Delivery and release boundary
- Maintain draft scope and staging-only, read-only acceptance. No service restart, real Neo4j query, write, router dispatch, provider spend, NAS mutation, production credentials, live auth probe or deployment authorized.
- Merge order only after parent #138 acceptance and separate operator review. A passing isolated test does not approve integration with `main`.
- Rollback: revert strict trace GET dependency references and detail Path limit on the draft branch. Original production injection remains unchanged.
