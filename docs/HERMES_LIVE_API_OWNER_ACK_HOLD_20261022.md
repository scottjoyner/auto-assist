# Hermes live heartbeat acknowledgment and AssistX owner gate — October 22, 2026

**Release decision: HOLD / NO-GO.** Draft auto-assist PR #425; P0 security issue #414 stays OPEN. All operations in this slice were read-only against deployed AssistX/production Hermes; task mutations and real model/provider dispatch were **zero**.

## Production API evidence obtained with Fleet Commander

The live `assistx-api` Docker container on x1-370 is running its expected `uvicorn assistx.api_router:app` entrypoint and responds to **GET `http://127.0.0.1:8000/openapi.json`**. Its published OpenAPI schema includes the task claim, heartbeat, completion, executor-token issuance, and task-claim status routes. No task claim or heartbeat request was made.

The deployed API's Docker configuration **does not enable `ASSISTX_REQUIRE_OWNER_BINDING=true`**. The source implementation defaults that switch to false. Thus, although the middleware and routes are registered, **production has not been proven to enforce canonical owner binding**. This is a concrete release blocker. It must not be addressed by setting a flag blindly until trusted gateway owner identity, real task ownership, and multi-user/revocation acceptance are verified.

The new `tests/hermes_live_api_owner_gate_preflight.py` checks only Docker metadata and the public OpenAPI route catalog, and it returns a JSON decision of HOLD with booleans, no environment/credential values, no user records, and no task mutation. Without the explicit physical flag the preflight is inert. It never grants a production cutover. Offline tests cover missing owner mode, wrong entrypoint, missing routes, stopped/ambiguous Docker metadata, and secret-value non-disclosure.

Command on x1-370:

    python3 tests/hermes_live_api_owner_gate_preflight.py --physical-readonly

Observed **read-only result:** expected API process true, all five routes true, owner-binding enabled **false**, independently administered custody **false**, release authorization **false**, zero task mutations.

## Correct worker-side heartbeat admission

The deployed API's POST `/api/tasks/{task_id}/heartbeat` handler returns a JSON object containing an updated authoritative Task record under the `task` key. The previous strict Hermes worker only checked HTTP status. A 200 response with an absent, revoked, expired, or differently owned task could therefore be accepted as healthy.

When the existing explicit research flag `HERMES_REQUIRE_OWNER_BOUND_LAUNCH=true` is set, the worker now checks **the actual Task echo**: signed task ID, claim ID, claiming agent, session ID, canonical owner issuer+subject, RUNNING status, and a live expiry at least as long as the signed token expiry. A malformed/missing Task body, stale/mismatched claim, revoked/PAUSING status, changed owner, or expired/shortened lease raises `LaunchDenied`. A semantically denied initial heartbeat or explicit heartbeat immediately fences the local lease. The background heartbeat loop also fences immediately on semantic denial instead of waiting for the usual transient transport-error retry budget. Transient HTTP/network failures still use their existing bounded retry policy.

Negative offline tests verify that HTTP 200 with wrong task, claim, agent, session, owner, status, lease or no Task cannot authorize a subprocess. The original synthetic container was upgraded to use the real endpoint's `{"task":...}` reply shape, and a mandatory **16th physical check** proves that a contradictory 200 Task owner is denied before any fake model subprocess, while preserving signed corpus, restricted RO/RW mounts, and actual SIGTERM-resistant OS process-group fencing.

The local worker's signed Task echo check is defense in depth; it does **not** substitute for independently enforced, authenticated server-side Task ownership, API claim middleware, signing-key custody or rollback-resistant witness checks. Production Hermes remains on its original exact container image with 16 legacy mounts/nine writable.

## Remaining release requirements

Independently administered high-water custody and key provenance, activated and physically witnessed API owner binding, real owner-authenticated claim/heartbeat/completion/receipt and revoke-during-run across partitions, removal of the 16 legacy mounts in favor of approved signed RO corpus and isolated RW scratch, signed release image, and verified rollback. Retain production HOLD, PR #425 DRAFT, issue #414 OPEN.
