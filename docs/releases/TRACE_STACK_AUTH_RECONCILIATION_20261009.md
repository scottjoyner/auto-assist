# Integration prospectus — trace GET auth boundary on complete evidence UI stack
Date: 2026-10-09 05:xx America/New_York. Research checkout: review/trace-auth-reconciled-20261009, isolated from upstream PRs.

## Prior evidence (not a blind hypothesis)
- Draft PR #140 reconciles UI keyboard focus/retry with #121→#122→#124→#128→#129 but still uses permissive _default_auth on the three sensitive trace GET routes.
- Draft PR #141 independently fixes those GET routes to fail closed when api.py operator authentication was not injected; its base is a different RC branch.
- Standalone swarm_routes._default_auth can return "system" without a configured auth dependency, or accept a Basic username without validating a password.
- Prior stack passed 102 Python, 22 Node and 21 loopback Chromium synthetic checks. That is not real production-auth evidence.

## Predictions and controls
1. Introduce a dedicated _trace_read_auth applied only to GET /api/traces, GET /api/traces/{correlation_id}, and GET /api/traces/{correlation_id}/evidence; require a configured operator auth dependency and nonblank principal before opening graph sessions.
2. Reject unauthenticated, Basic-username-only, invalid credentials, 401/403 from injected auth, null/blank principal, overlong trace IDs, and all such errors before _neo() is called.
3. Authorized fixture operator identity retains read-only index, detail, and opt-in evidence behavior and the UI's global filtering/context contract. Trace event POST and unrelated swarm endpoints are not modified.
4. Tests must verify the same security invariant on an isolated FastAPI TestClient and targeted parent UI/backend regressions. Do not request credentials, use production Neo4j, providers, user traces, Redis/NAS, services, or merge changes into production.
5. It is NOT a proof of real api.py configured auth, reverse-proxy identity, production rate limits, or physical Neo4j read fencing. Redis loss gate #148 and release #123 remain NO-GO.

## Decision criteria
Hold review branch in draft until all negative admissions and existing 102+22+21 source-integrated tests pass, then document exact differences and publish an isolated stacked PR. Even if tests pass, no activation or service restart is authorized.


## Observed integration acceptance — October 9

The existing draft PR #141 security patch was integrated on top of the reconciled trace UI/evidence stack (draft #140). Three GET routes now fail closed without the existing api.py-injected operator authentication: trace index, trace detail, and opt-in trace evidence. Standalone Basic usernames or fallback system identity are not accepted. Null or whitespace principal is rejected. The trace detail correlation ID is bounded to 1–128 characters. No POST route or other swarm endpoint authorization was changed.

The focused upstream negative-admission suite from #141 was imported. One inherited test from the global-filter stack required a *test fixture* correction: it previously overrode the old permissive auth dependency. That override now rightly returns 401. The fixture was changed to use an invented operator and synthetic password verified through the injected auth callback; invalid query-parameter assertions now send valid synthetic credentials to reach validation.

Measured on x1-370:
- 122 Python backend tests PASS (auth, outcome, context, task evidence, research attestation), 1 existing Starlette TestClient deprecation warning.
- 22 Node VM UI tests PASS.
- 21 synthetic Chromium checks PASS (375/768/1440, keyboard focus, opt-in disclosure, 401/429/503 recovery, GET-only).
- Python compilation, JS syntax and git diff validation PASS.

These are the full current tests for this integration; earlier 102 Python / 22 Node / 21 browser results are not separate additions.

Decision: integration has passed synthetic tests, but remains an unmerged draft, with no production auth proof. Issues #123 and #148 still block actual authenticated browser/load acceptance and physical in-flight query fencing. Issue #125 independently blocks node/agent execution identity, #117 blocks full historical trace custody, and #145 tracks red RC CI contracts. The rate-limit/lease research drafts #147/#154/#160/#166 are separate and are not activated by this patch. No operational source, NAS, Redis, Neo4j, agent routing, provider admission, external model or service was modified.
