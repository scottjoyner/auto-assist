# OBS-RC1: isolated full FastAPI auth and trusted-header acceptance

**Date:** 2026-10-08 EDT / 2026-10-09 UTC. **Scope:** approval-bounded synthetic research; no deployed auth requests, provider calls, fleet dispatch or production Neo4j/Redis access.

## Pre-test prediction and objective

1. A **real app import** with the canonical `api.auth` injected into the trace router, using production dependency mode but synthetic Basic credentials, should reject anonymous and incorrect password requests to `/traces` plus three GET trace routes **before** any graph access.
2. Valid synthetic Basic should render the page and retrieve strictly mocked index/detail/evidence responses without touching Neo4j. Oversized IDs and limit should return 422; mode `off` must not check quota.
3. An opt-in synthetic shared-quota result should return 429 with `Retry-After`; quota outage should return 503 with `Retry-After` before graph access. Auth must precede budget. No live Redis is used.
4. If `TRUSTED_AUTH_HEADER` is nonempty, code in `api._auth_user_from_credentials` accepts the configured header's asserted username before Basic credentials; a synthetic header-only request is expected to succeed in isolation. **This is a code-level conditional, not evidence that real ingress allows caller-supplied headers.**
5. The production-like container can run with no network, no host ports, no provider credentials, read-only source imports, read-only root filesystem, resource limits and explicit image+source pinning.

## Tested implementation

- `scripts/trace_read_api_auth_canary.py` contains both synthetic scenarios and refuses a run without an explicit scenario marker and fixed invented credentials. It imports `assistx.api.app` (not a mock app), checks that `swarm_routes` has the canonical injected `api.auth`, then replaces only Neo4j construction/read functions with in-process data.
- `scripts/run_trace_api_isolated_canary.py` is an explicit opt-in Docker runner that requires **both** an immutable cached image digest and exact expected git commit SHA. It imports source, templates, static and only the one test script read-only. It does not mount the `.git` directory, fleet credentials or NAS.
- Docker isolation: `--rm --pull never --network none --ipc none --cpus 0.5 --memory 768m --pids-limit 100 --read-only --tmpfs /tmp:rw,nosuid,size=64m --cap-drop ALL --security-opt no-new-privileges`. Uses `env -i` with synthetic Basic credentials and production dependency profile (`ASSISTX_DEPENDENCY_MODE=production`, `ASSISTX_RUNTIME_PROFILE=production`). Nothing is served on a real TCP port; the client is Starlette/FastAPI TestClient in the process.
- Cached local production-dependency image used for this test, **never the running AssistX service**: `sha256:9636c7b5776738cd23263a6a5ebdc79c3476e0aefc724ea5e57eef1e548d6a1c`. Image provenance is local cache, not a production deployment attestation.
- `tests/test_trace_api_isolated_canary_guard.py` requires opt-in, a pinned digest and synthetic mode and statically checks Docker isolation. General GitHub Actions runs **guards only**; it does not launch Docker.

## Observations

- The strict-Basic scenario returned 401 to absent/incorrect Basic and forged forwarded/synthetic identity headers on `/traces`, trace index, trace detail and explicit task evidence. The three accepted synthetic GETs returned 200; mocked graph objects were closed. Overlong IDs and page bounds returned 422 without opening graph sessions.
- Optional rate-budget mode `off` did not call a quota adapter. With an injected synthetic quota denial each trace GET returned 429 + `Retry-After=11`; quota-store failure returned 503 + `Retry-After=5`; neither path opened graph. Invalid budget mode yielded 503. The isolated harness printed `ISOLATED_FASTAPI_TRACE_AUTH_CANARY_PASS`.
- With `TRUSTED_AUTH_HEADER=X-Synthetic-Proxy-Identity` (invented canary header), a header-only GET returned **200 without Basic** while no-header GET returned **401**; `/traces` also returned 200 with the synthetic header. This was deliberately tested only in the isolated container. The harness printed `CONDITIONAL_TRUST_BOUNDARY_BLOCKED_UNTIL_UPSTREAM_STRIPPING_PROVEN`.
- Read-only running-container inventory (flags only, no values) shows live `assistx-api` has `BASIC_AUTH_USER`, `BASIC_AUTH_PASS`, and `TRUSTED_AUTH_HEADER` configured; localhost binding is `127.0.0.1:8000`. This does **not** prove header injection is possible from any external ingress; see P0 issue #149.
- Local offline pytest after adding guard coverage: **127/127 pass** (one existing Starlette deprecation warning). The exact published code/CI commit must be independently rechecked; local scratch test does not supply a published commit receipt.

## Neo4j 5.26 staging status — honest boundary

- The cached exact image `neo4j:5.26.26-enterprise`, SHA `sha256:0f4ab99cde6cfc71f7a268b93e4db8fb64c22faaef7a8a0df19483bd17e8f858`, was used to attempt a disposable no-network/no-published-port, 0.5 CPU / 1.5 GiB instance with temp data. Strict read-only/capability startup failed due entrypoint configuration and permission requirements. A relaxed ephemeral container also exited with code 3 before Bolt readiness. It was removed and the running `neo4j` service was not accessed.
- **No Neo4j 5.26 query plan, DB hits, p50/p95/p99 or 85,000-group proof was obtained.** Do not reuse the earlier Neo4j 5.23 synthetic benchmark as 5.26 staged capacity acceptance.
- Next research should use a vetted 5.26 test-compose profile with normal Neo4j startup permissions, no published host port, disposable data and separate resource checks; then a generated dataset and bounded driver-level timings, not a production PROFILE.

## Unclosed gates

1. **P0 ingress trust (#149):** verify proxy strips caller-supplied trusted identity header and injects it only after independent identity verification, with no alternate ingress. The Basic route has no distinct operator RBAC proof. Until established, live authentication acceptance is **NO-GO**.
2. **P0 real staging:** authenticated session expiry, 401/403, 429/503, rate-limit store/secret custody and the operational consequence of failing closed still unverified.
3. **P0 query capacity:** Neo4j 5.26 staging p95/p99 with 85k synthetic groups and bounded query-plan/DB-hit evidence. No production connection was made.
4. **P0 baseline CI:** source-binding/recovery-canary pre-existing failures (issue #143), independent from this observability work.
5. **P1 accessibility + rollback:** mobile/touch/screen-reader and staged asset rollback remain operator-reviewed.

**Decision:** Publish as a separate draft acceptance PR, not production authorization. Do not enable the quota switch, trusted-header bypass, Usage & burn endpoints, provider-budget admission or agent execution on this evidence.
