# Read-only free-provider throttling hardening — 2026-10-09

Scope: `scripts/free_subagent_supervisor.py::provider_state` and focused offline regression tests. This is **not** an activated routing policy or production readiness claim.

## Hypothesis
Read-only evidence reporting HTTP 429 or 503, including string-valued status codes or common throttle aliases, should exclude the affected model from the candidate pool. Another independently qualified free model may remain eligible; no paid model should be selected.

## Implementation and observation
The provider-state classifier now treats the documented throttle aliases and numeric/string HTTP 429 and 503 as `rate_limited`. `order_free_pools` already excludes anything not `usable`; this change reuses that existing boundary. It does not implement transport retry timers, retry-after enforcement, provider quota coordination, or live cost custody.

The original agent commit `7f78a555` passed 17 new synthetic tests, but those two generated root-level files were oversized and contained unrelated assertions. Review replaced them with a focused `tests/test_free_provider_rate_limit_errors.py` suite using real route classifier and pool selection calls, and removed unsupported readiness language.

## Gates remaining
- Validate with actual Kilo/other FREE provider 429 receipts, including HTTP response normalization and reliable event provenance; catalog success alone is insufficient.
- Link token usage and cost receipts to a durable per-attempt OpenCode trace (separate trace-contract branch).
- Review candidate ordering, admission, quota lockout and no-paid fallback under real multi-node concurrency before activation.
- Independent CI and branch reconciliation are required. No production dispatch or deployment was authorized in this sprint.
