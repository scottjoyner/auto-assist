# Provider burn-down: offline research acceptance and evidence
Date: 2026-10-08 UTC
Scope: synthetic only. No production server, real provider token/credit calls, login, dispatch, admission, retry or quota enforcement.

## Problem and prediction (pre-test prospectus)
1. A meter with declared source authority, matching account/model/source/unit/window, fresh monotonically growing readings and a known reset should yield a measured burn rate and (only if before reset) an exhaustion prediction.
2. Error responses, stale data, unverified quotas and missing windows should display **unknown/unavailable**, not zero.
3. Cross-provider/model/account, different window, duplicate conflicting timestamps and rollback should prevent forecasting.
4. A measured burn rate remains meaningful when a reset happens before predicted exhaustion, but no exhaustion timestamp should be emitted.
5. No source is independently authenticated by this library; `authoritative=true` is a caller-side declared flag, **not** proof of genuine provider quota.

## Bounded implementation
- Pure synchronous model: `static/js/provider_burn_model.js`; usable in Node or browser, but **not** imported by the deployed UI.
- Fixtures: `tests/test_provider_burn_model.cjs`.
- Input includes `provider`, `model`, `source`, non-secret `account_alias`, `unit`, `observed_at`, `window_start`, `window_end`, `reset_at`, `limit`, `used`, `authoritative`, optional `http_status`.
- Output retains explicit `status`, `reason`, `remaining`, `burn_per_hour`, `projected_exhaustion_at`, `forecast_reason`, and `sample_count`. Unknown numbers stay `null`.
- Measured burn is calculated only from three or more distinct, monotonic, identity-matched samples spanning >=60 seconds; timestamps are UTC or offset-aware. No rate is inferred from an incomplete or stale meter.
- All values synthetic; never pass API secrets as aliases. Browser integration must keep Usage & burn link staged until its authenticated API endpoints are actually available.

## Observation (after implementation)
- Date: 2026-10-08 21:54 UTC.
- Isolated x1-370 checkout: `/tmp/assistx-burn-acceptance-QjiLHijn/repo` (not shared /nas worktree).
- Exact tested head: `4b1ed435c2fc5af4d9af40fc4df05fd0750704e3`.
- Command: `node --check static/js/provider_burn_model.js && node --check tests/test_provider_burn_model.cjs && node --test tests/test_provider_burn_model.cjs`.
- Outcome: **15/15 pass; 0 fail**. Both JS syntax checks pass.
- Sources: synthetic fixture objects only. **No live provider-budget accuracy, production API, browser integration, authenticated session, or actual provider quotas were evaluated.**

## Remaining blockers and decision
- **Do not activate** `/provider-usage` or `/api/fleet/provider-burn` merely because this model passes. Previous routes returned 404 and must be verified separately.
- Production adapter must independently validate identity, authentication, quota semantics, reset windows, server time, rate limit/401, fresh observations, and redaction. It must never conflate client-local token usage with provider-authoritative remaining quota.
- Before UI integration: property-based tests for overflow and timezone boundary, stale-window warning labels, CSP check, and authenticated browser failure states.
- Before operational decisions: provider-budget enforcement belongs to the separate allocator/lease authority acceptance, not this dashboard.
- Trace UI follows draft #121 -> draft #122 -> draft #124 -> draft #128; keyboard/detail recovery draft #131 remains stacked on #121.
- Reference: `docs/ASSISTX_OBSERVABILITY_NEXT_SLICE_20261008.md` on the independent prospectus branch.

## Audit
Read-only execution occurred against an isolated checkout; repo writes were additive on a dedicated GitHub branch. No deployment, merge, data migration, credentials, NAS recovery edits or fleet task execution authorized.
