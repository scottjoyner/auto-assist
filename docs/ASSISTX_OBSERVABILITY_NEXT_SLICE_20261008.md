# AssistX observability — next bounded acceptance slice
Date: 2026-10-08 EDT
Authority: documentation and synthetic/read-only validation only. No merge, deployment, live provider calls, fleet dispatch, database writes, or service restarts.

## Baseline and dependencies
- Draft #121 trace UI -> draft #122 global outcome -> draft #124 context -> draft #128 registry evidence.
- All four drafts remain unmerged at planning time. Do not merge or rebase implicitly.
- Existing provider-usage and provider-burn live routes previously returned 404. Keep Usage & burn staged until independently authenticated and deployed.
- #123 is the open acceptance tracker; #117 is independent full-fidelity trace custody.

## Prospectus / predictions (recorded before next experiment)
H1: A 375px / 768px / 1440px authenticated browser run will reveal any remaining focus-loss or overflow missed by browserless DOM tests.
H2: Replacing trace list innerHTML on selection can lose focused row; test focus continuity after Enter.
H3: After a detail 503, Refresh does not re-request detail while state.selected is populated; verify with intercepted GET count.
H4: A 401 on index refresh leaves previously selected detail DOM present; verify no stale payload remains visible.
H5: Provider burn-down can be computed only for sources that expose explicit quota units, authoritative window/reset, and timestamped usage; all other sources should show unknown, not zero.

## Track A — parent-only browser acceptance
- Run PR #121 exact assets, not stacked child assets, with synthetic same-origin intercepted responses first.
- Browser viewports: 375x812, 768x1024, 1440x900. Assert no page horizontal overflow, visible nav, list, detail, and usable buttons.
- Keyboard: skip link, Tab and Shift+Tab, Enter on rows, visible focus and selected aria-pressed; verify focus is not lost when row DOM is replaced.
- Direct link: ?trace=OUTSIDE_FIRST_PAGE opens detail by exact encoded ID and leaves no event payload in URL.
- Event disclosure: no payload text in DOM before open, inserted via textContent on open, cleared on close, 64KiB display cap; test script-like strings.
- Retry: 401/403 index errors, 429/503, subsequent successful retry; 503 detail then Refresh; stale response fencing.
- Security: anonymous index and detail GET rejected by actual middleware; authenticated index/detail preserve original rate limit, 429 and 401/403 semantics. Do not run load/stress tests against production.
- Staged boundary: Usage & burn is non-clickable while /provider-usage and /api/fleet/provider-burn are unverified/404.
- Evidence: commit SHA, fixture hash, browser version, viewport, test case, result, screenshot path with no sensitive data, timestamp. Sanitize URLs, cookies, trace payloads and identifiers.
- Fail closed on stale detail after auth failure; show unknown metrics instead of misleading zero.

## Track B — burn-down read-only design
- Contract fields: provider, account/credential alias (never secret), model, source, unit (tokens/requests/USD), window_start/end, reset_at, limit, used, remaining, observed_at, freshness, authoritative flag, confidence, error.
- Predictions: a provider with trusted limit and reset yields a finite forecast; a provider with only local tokens yields observed usage but no authoritative remaining quota; a 404, 401, or timeout yields unavailable/unknown.
- Projected exhaustion: only when limit and used share the same unit and window, timestamps are valid, and measured burn rate has a sufficient sample; otherwise no ETA. Separate zero usage from unavailable telemetry.
- Never expose credential values or infer a free-provider quota from an undocumented response. No automatic provider calls or budget admission changes.
- Acceptance: mocked 200/401/404/429/503, stale timestamps, quota resets, mismatched units, counter rollbacks, duplicates, missing windows, and overlapping provider identities.

## Track C — trace lineage
- Evidence join keys: correlation_id, task_id, dispatch_id, route_id, provider_call_id, node registry ID; mark source IDs unverified until independent attestation.
- Show explicit provenance, source timestamp, confidence, ambiguity and missing joins.
- No new backend endpoint in this slice. No machine verified badge, no execution/retry/cancel controls.
- Operator navigation is read-only, contextual, and lazy; no automatic registry lookups or payload disclosure.

## Exit gate
Parent #121 passes authenticated browser/device accessibility and existing auth/rate-limit checks; three identified UI regressions are dispositioned; separate provider burn endpoints remain staged until actually deployed. Record predictions, observations, failures, and rollback plan. No merge or deployment without separate operator approval.
