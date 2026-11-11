# Auto-Assist free-pool status: fail-closed freshness repair

**Date:** 2026-11-10 (America/New_York). **Scope:** isolated source branch only.

## Reproduced defect

Prior `scripts/free_pool_status.py` set `evidence_fresh` from the newest
subagent lifecycle timestamp, not from the saved model projection.
One fresh unrelated lifecycle entry could make a weeks-old projection
appear current, while exposing its prior `proven` or `trial` model tiers.

## Fix and public contract

- Require a recent regular-file model projection with the expected
  `free-pool-projection/v1` read-only, non-authoritative contract.
- Require a recent `free-pool-refresh-run/v1` receipt whose actual
  `finished_at` is recent, `executed=true`, and `paid_fallback=false`.
- Refuse missing, symlinked, oversized, malformed, expired, or
  future-dated files and incomplete contracts.
- Keep lifecycle and projection recency as separate reported booleans;
  `evidence_fresh` requires both.
- With incomplete projection evidence, publish zero current
  `models.proven` and `models.trial` entries while preserving historical
  counts for review. Never fabricate an eligible route.
- Explicitly report `dispatch_allowed=false`,
  `account_entitlement_proven=false`, and
  `shared_quota_lease_proven=false` regardless of local freshness.

## Verification and release boundary

Offline targeted acceptance:
`PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q -p no:cacheprovider tests/test_free_pool_status.py tests/test_free_pool_projection.py tests/test_free_pool_status_freshness_20261110.py`

Observed **28/28 passing**. A read-only check against x1-370's old
projection/refresh records returned `projection_fresh=false`,
`evidence_fresh=false`, `dispatch_allowed=false`, and zero currently
published proven/trial model entries.

**Not authorization:** No actual provider call, account quota readback,
model inference, paid fallback, shared lease, key rotation, route change,
process restart, timer update, merge, or deployment was undertaken.
Local freshness does not establish provider billing/entitlement, privacy,
source custody or execution identity. This branch is based on historical
`agent/free-pool-status-20261006`, not current main; an independently
reviewed integration path is required before deployment.
