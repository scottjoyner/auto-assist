"""Offline-only aggregation of projected FleetExecutor response evidence.

The input is a caller-supplied projection, not a signed completion receipt.
Equal projections are replay candidates, not proof of a single model call.
Never use this module to authorize dispatch, count accepted work, or bill.
"""
from collections import Counter
import hashlib
import json

MAX_ROWS = 5000
MAX_ID_LENGTH = 512
TOKEN_MAX = (1 << 63) - 1
FIELDS = {"task_id", "claim_id", "projection_generation", "projection_revision",
          "response_accounting"}
COUNT_FIELDS = ("reported_prompt_tokens", "reported_completion_tokens",
                "reported_total_tokens", "reconciled_reported_total_tokens")
ACCOUNT_FIELDS = {"schema", "usage_state", *COUNT_FIELDS, "issues",
                  "response_state", "provider_execution_verified",
                  "independent_quality_verified", "accepted_work_units",
                  "billing_verified"}
FINISH_STATES = {"HTTP_NOT_SUCCESS", "NO_NONEMPTY_TEXT", "TEXT_STOP_REPORTED",
                 "TEXT_TRUNCATED_REPORTED", "TEXT_FINISH_UNKNOWN",
                 "TEXT_OTHER_FINISH"}
ISSUE_CODES = {"invalid_prompt_tokens", "invalid_completion_tokens",
               "invalid_total_tokens", "invalid_usage_object",
               "token_sum_overflow", "inconsistent_total_tokens"}


class InvalidProjection(ValueError):
    """Fixed reason only; callers must not log raw input."""


def _id(value):
    return type(value) is str and 0 < len(value) <= MAX_ID_LENGTH


def _int(value):
    return type(value) is int and 0 <= value <= TOKEN_MAX


def _validate(row):
    if type(row) is not dict or row.keys() != FIELDS:
        raise InvalidProjection("invalid_envelope")
    if not all(_id(row[k]) for k in ("task_id", "claim_id", "projection_revision")):
        raise InvalidProjection("invalid_lineage")
    if not _int(row["projection_generation"]):
        raise InvalidProjection("invalid_generation")
    a = row["response_accounting"]
    if type(a) is not dict or a.keys() != ACCOUNT_FIELDS:
        raise InvalidProjection("invalid_accounting_shape")
    if a["schema"] != "fleet-router-response-accounting-v1":
        raise InvalidProjection("unsupported_schema")
    if any(a[k] is not False for k in ("provider_execution_verified",
            "independent_quality_verified", "billing_verified")) or a["accepted_work_units"] is not None:
        raise InvalidProjection("unsupported_authority_claim")
    if type(a["response_state"]) is not str or a["response_state"] not in FINISH_STATES:
        raise InvalidProjection("invalid_response_state")
    if any(a[k] is not None and not _int(a[k]) for k in COUNT_FIELDS):
        raise InvalidProjection("invalid_count")
    issues = a["issues"]
    if (type(issues) is not list or len(issues) > len(ISSUE_CODES)
            or any(type(x) is not str or x not in ISSUE_CODES for x in issues)
            or len(set(issues)) != len(issues)):
        raise InvalidProjection("invalid_issue_codes")
    p, c, supplied, reconciled = (a[k] for k in COUNT_FIELDS)
    if issues:
        expected = "INVALID"
        expected_total = None
        if "invalid_usage_object" in issues and any(x is not None for x in (p, c, supplied)):
            raise InvalidProjection("inconsistent_invalid_usage")
        for field, count in (("prompt_tokens", p), ("completion_tokens", c), ("total_tokens", supplied)):
            if "invalid_" + field in issues and count is not None:
                raise InvalidProjection("inconsistent_invalid_count")
    elif p is not None and c is not None:
        if p + c > TOKEN_MAX or (supplied is not None and supplied != p + c):
            raise InvalidProjection("inconsistent_reported_total")
        expected = "COMPLETE_REPORTED"
        expected_total = p + c
    else:
        expected = "PARTIAL_REPORTED" if any(x is not None for x in (p, c, supplied)) else "NOT_REPORTED"
        expected_total = None
    if a["usage_state"] != expected or reconciled != expected_total:
        raise InvalidProjection("inconsistent_accounting_state")
    return a


def summarize_projections(rows):
    """Summarize a bounded in-memory list; no I/O, model calls or graph writes.

    Any malformed projection or conflicting task/claim pair suppresses totals.
    Partial/missing/invalid usage is never zero-filled. A changed generation
    or revision for the same task/claim pair is a conflict, not a new attempt.
    """
    if type(rows) is not list or len(rows) > MAX_ROWS:
        raise InvalidProjection("batch_limit_or_type")
    groups = {}
    conflicted = set()
    errors = Counter()
    replay_rows = 0
    for row in rows:
        try:
            a = _validate(row)
        except InvalidProjection as exc:
            errors[str(exc)] += 1
            continue
        key = (row["task_id"], row["claim_id"])
        # Compare only the exact accounting projection; no raw content retained.
        normalized = dict(a, issues=sorted(a["issues"]))
        payload = [row["projection_generation"], row["projection_revision"], normalized]
        witness = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest()
        if key not in groups:
            groups[key] = ({witness}, a)
        else:
            witnesses, _ = groups[key]
            if witness in witnesses:
                replay_rows += 1
            else:
                witnesses.add(witness)
                conflicted.add(key)
    good = [a for k, (_, a) in groups.items() if k not in conflicted]
    usage = Counter(a["usage_state"] for a in good)
    responses = Counter(a["response_state"] for a in good)
    complete = [a for a in good if a["usage_state"] == "COMPLETE_REPORTED"]
    held = bool(errors or conflicted)
    return {
        "schema": "fleet-router-accounting-summary-shadow-v1",
        "decision": "HOLD" if held else ("EMPTY" if not rows else "PROJECTED_ONLY"),
        "input_rows": len(rows),
        "malformed_rows": sum(errors.values()),
        "malformed_reasons": dict(sorted(errors.items())),
        "distinct_attempt_keys": len(groups),
        "conflicting_attempt_keys": len(conflicted),
        "identical_projection_replay_rows": replay_rows,
        "nonconflicting_attempt_keys": len(good),
        "usage_states_nonconflicting": dict(sorted(usage.items())),
        "response_states_nonconflicting": dict(sorted(responses.items())),
        "complete_reported_attempts": len(complete),
        "reported_total_complete_subset": (sum(a["reconciled_reported_total_tokens"] for a in complete)
                                           if complete and not held else None),
        "reported_total_all_attempts": (sum(a["reconciled_reported_total_tokens"] for a in complete)
                                        if good and len(complete) == len(good) and not held else None),
        "actual_endpoint_calls": None,
        "independently_accepted_work_units": None,
        "verified_endpoint_receipts": None,
        "source_custody_verified": False,
        "dispatch_authorized": False,
    }
