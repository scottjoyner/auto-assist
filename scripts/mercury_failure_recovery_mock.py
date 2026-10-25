"""Provider-free Mercury failure/fallback classification fixture.

Pure policy projection only: no HTTP, model, token, worker, timer, or authority.
A computed cooldown deadline is NOT a permission to attempt execution later.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_GROUP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_MAX_MS = 2**53 - 1
_RATE_LIMIT_FLOOR_MS = 15 * 60 * 1000
_UNAVAILABLE_FLOOR_MS = 5 * 60 * 1000
_MAX_RETRY_AFTER_MS = 24 * 60 * 60 * 1000
_FAILURE_KINDS = {"timeout", "disconnect", "network_error", "unknown_ack"}


def _bounded_ms(value: Any) -> bool:
    return type(value) is int and 0 <= value <= _MAX_MS


def _safe_group(value: Any) -> str | None:
    return value if isinstance(value, str) and _GROUP_ID.fullmatch(value) else None


def project_failure(
    *,
    upstream_group: Any,
    status_code: Any = None,
    failure_kind: Any = None,
    observed_ms: Any,
    retry_after_ms: Any = None,
    previous_cooldown_until_ms: Any = None,
    previous_observed_ms: Any = None,
    fallback_group: Any = None,
) -> dict[str, Any]:
    """Create a redacted deterministic DENY receipt from synthetic observations.

    No input can confer a grant. A real system would need independently trusted
    clock, upstream-group alias map, atomic account spend authority and signed
    revocation/custody before even considering a fresh request.
    """
    group = _safe_group(upstream_group)
    other = _safe_group(fallback_group) if fallback_group is not None else None
    reasons: set[str] = {"unverified_resource_owned_quota_authority"}
    need_owner_review = False
    until = None

    clock_ok = _bounded_ms(observed_ms)
    if not clock_ok:
        reasons.add("invalid_observation_clock")
        need_owner_review = True
    if previous_observed_ms is not None:
        if not _bounded_ms(previous_observed_ms):
            reasons.add("invalid_previous_clock")
            need_owner_review = True
        elif clock_ok and observed_ms < previous_observed_ms:
            reasons.add("clock_rollback")
            need_owner_review = True

    if previous_cooldown_until_ms is not None:
        if not _bounded_ms(previous_cooldown_until_ms):
            reasons.add("invalid_prior_cooldown")
            need_owner_review = True
        else:
            until = previous_cooldown_until_ms

    if group is None:
        reasons.add("unknown_upstream_group")
        need_owner_review = True
    if fallback_group is not None:
        if other is None:
            reasons.add("invalid_fallback_group")
        elif other == group:
            reasons.add("same_upstream_group_not_independent_quota")
        else:
            reasons.add("cross_group_fallback_unverified")
        # Fallback is never allowed merely because another alias is present.
        need_owner_review = True

    if failure_kind is not None:
        if isinstance(failure_kind, str) and failure_kind in _FAILURE_KINDS:
            reasons.add("unknown_spend_outcome_quarantine")
        else:
            reasons.add("invalid_failure_kind")
        need_owner_review = True
    elif type(status_code) is not int:
        reasons.add("missing_or_invalid_provider_status")
        need_owner_review = True
    elif status_code in (401, 402, 403):
        reasons.add("credentials_or_billing_hold")
        need_owner_review = True
    elif status_code in (429, 503):
        reasons.add(
            "upstream_rate_limited" if status_code == 429
            else "upstream_unavailable"
        )
        floor = (_RATE_LIMIT_FLOOR_MS if status_code == 429
                 else _UNAVAILABLE_FLOOR_MS)
        delay = floor
        if retry_after_ms is not None:
            if not _bounded_ms(retry_after_ms) or retry_after_ms > _MAX_RETRY_AFTER_MS:
                reasons.add("retry_after_untrusted_requires_review")
                need_owner_review = True
            else:
                delay = max(delay, retry_after_ms)
        if clock_ok:
            if observed_ms <= _MAX_MS - delay:
                until = max(until or 0, observed_ms + delay)
            else:
                reasons.add("cooldown_overflow")
                need_owner_review = True
    else:
        # 200 is also unauthenticated/unattributed here; it cannot clear a
        # prior 429 or an ambiguous vendor-side spend.
        reasons.add("unverified_provider_outcome")
        need_owner_review = True

    result = {
        "schema": "mercury-failure-deny-mock/v1",
        "decision": "DENY",
        "dispatch_authorized": False,
        "automatic_retry_authorized": False,
        "automatic_fallback_authorized": False,
        "provider_calls": 0,
        "worker_launches": 0,
        "verified_cost_usd": None,
        "upstream_group": group,
        "fallback_group": other,
        "advisory_cooldown_until_ms": until,
        "requires_owner_review": need_owner_review,
        "reasons": sorted(reasons),
        "trace_custody": "UNATTESTED",
        "quota_authority": "UNVERIFIED",
    }
    # Hash redacted canonical metadata, never prompts/tokens/error bodies.
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    result["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result
