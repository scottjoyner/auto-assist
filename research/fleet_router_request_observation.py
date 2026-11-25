"""Unwired, offline request-observation contract for accounting research.

The observation_id MUST be allocated by the instrumenting router at the
attempt boundary and retained for retries of evidence publication. This
module cannot attest that the caller actually did so. Request metadata is
only claimed lineage, never authentication or authorization. No I/O.
"""
import hashlib
import json

SCHEMA = "fleet-router-request-observation-shadow-v1"
MAX_ID = 512
MAX_ROWS = 5000
MAX_COUNT = (1 << 63) - 1
TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


class InvalidObservation(ValueError):
    """Fixed reason only, never include untrusted values."""


def _ident(v):
    return type(v) is str and bool(v.strip()) and v == v.strip() and len(v) <= MAX_ID


def _count(v):
    return type(v) is int and 0 <= v <= MAX_COUNT


def _usage(usage):
    counts = {}
    issues = []
    for key in TOKEN_FIELDS:
        v = usage.get(key) if type(usage) is dict else None
        counts[key] = v if _count(v) else None
        if v is not None and not _count(v):
            issues.append("invalid_" + key)
    if usage is not None and type(usage) is not dict:
        issues.append("invalid_usage_object")
    p, c, t = (counts[k] for k in TOKEN_FIELDS)
    derived = None
    if p is not None and c is not None:
        if p + c > MAX_COUNT:
            issues.append("sum_overflow")
        elif t is not None and t != p + c:
            issues.append("total_mismatch")
        elif not issues:
            derived = p + c
    if issues:
        state = "INVALID"
    elif derived is not None:
        state = "COMPLETE_REPORTED"
    elif any(v is not None for v in counts.values()):
        state = "PARTIAL_REPORTED"
    else:
        state = "NOT_REPORTED"
    return {"state": state, "reported": counts, "derived_total": derived, "issues": issues}


def _lineage(request):
    meta = getattr(request, "metadata", None)
    if type(meta) is not dict:
        return {"state": "NOT_DECLARED", "claimed": None}
    value = meta.get("assistx_executor")
    if value is None:
        return {"state": "NOT_DECLARED", "claimed": None}
    keys = ("task_id", "claim_id", "agent_id", "projection_generation")
    if (type(value) is not dict or any(not _ident(value.get(k)) for k in keys[:3])
            or not _count(value.get(keys[3]))):
        return {"state": "INVALID_DECLARATION", "claimed": None}
    claimed = {k: value[k] for k in keys}
    # Reject conflicting aliases, not silently select one identity.
    pairs = [(getattr(request, "task_id", None), claimed["task_id"]),
             (meta.get("task_id"), claimed["task_id"]),
             (meta.get("claim_id"), claimed["claim_id"]),
             (meta.get("agent"), claimed["agent_id"]),
             (meta.get("executor"), claimed["agent_id"]),
             (meta.get("runtime_projection_generation"), claimed["projection_generation"])]
    if any(actual is not None and (type(actual) is not type(expected) or actual != expected)
           for actual, expected in pairs):
        return {"state": "CONFLICTING_DECLARATION", "claimed": None}
    return {"state": "DECLARED_UNVERIFIED", "claimed": claimed}


def build_observation(*, observation_id, request, provider, model, stage,
                      status_code, usage=None, estimate=None, error=None):
    request_id = getattr(request, "request_id", None)
    if not all(_ident(x) for x in (observation_id, request_id, provider, model, stage)):
        raise InvalidObservation("invalid_observation_identity")
    if status_code is not None and (type(status_code) is not int or not 100 <= status_code <= 599):
        raise InvalidObservation("invalid_http_status")
    if error is not None:
        outcome = "ERROR_REPORTED"
    elif status_code is None:
        outcome = "STATUS_UNKNOWN"
    elif 200 <= status_code < 300:
        outcome = "HTTP_SUCCESS_REPORTED"
    else:
        outcome = "HTTP_NON_SUCCESS_REPORTED"
    estimates = {}
    for key in ("input_tokens", "total_tokens"):
        value = getattr(estimate, key, None)
        estimates[key] = value if _count(value) else None
    return {
        "schema": SCHEMA,
        "observation_id": observation_id,
        "request_id": request_id,
        "provider": provider, "model": model, "stage": stage,
        "status_code": status_code, "outcome": outcome,
        "lineage": _lineage(request),
        "usage": _usage(usage),
        "estimates": estimates,
        "physical_execution_verified": False,
        "lineage_authenticated": False,
        "accepted_work_units": None,
    }


def observation_key(observation):
    """Separate namespace; use a stable producer-supplied observation ID."""
    value = observation.get("observation_id")
    if not _ident(value):
        raise InvalidObservation("invalid_observation_identity")
    return "shadow-request-observation-v1:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def reconcile_built_observations(rows):
    """Deduplicate builder outputs in one bounded, trusted-process batch.

    Not an external import validator or an authenticated export. An exact
    replay counts once; a changed payload under one observation ID is HOLD.
    Never equate request IDs, stage observations or reported tokens with
    physical calls or productive work.
    """
    if type(rows) is not list or len(rows) > MAX_ROWS:
        raise InvalidObservation("observation_batch_limit")
    seen = {}
    replay = conflicts = 0
    for row in rows:
        if (type(row) is not dict or row.get("schema") != SCHEMA
                or row.get("physical_execution_verified") is not False
                or row.get("lineage_authenticated") is not False
                or row.get("accepted_work_units", 0) is not None):
            raise InvalidObservation("not_builder_observation")
        key = observation_key(row)
        digest = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode("utf-8")).hexdigest()
        if key in seen:
            if seen[key][0] == digest:
                replay += 1
            else:
                conflicts += 1
        else:
            seen[key] = (digest, row)
    unique = [row for _, row in seen.values()]
    return {
        "decision": "HOLD" if conflicts else "OBSERVATIONS_ONLY",
        "unique_observation_ids": None if conflicts else len(unique),
        "distinct_request_ids": None if conflicts else len({r["request_id"] for r in unique}),
        "exact_replays": replay,
        "conflicting_rows": conflicts,
        "actual_endpoint_calls": None,
        "independently_accepted_work_units": None,
        "verified_endpoint_receipts": None,
    }
