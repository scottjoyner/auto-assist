"""Deny-only, CPU-only Mercury worker admission reference. NO network and NO dispatch.

This is a mock model, NOT a verifier of cryptographic signatures, authenticated
leases, provider quota, upstream billing, or independent trace custody.
Use only for negative-path acceptance. Caller data never confers authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

CONTRACT = "mercury-worker-deny-mock/v1"
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
REQUIRED = (
    "root_run_id", "task_id", "action_id", "parent_action_id",
    "worker_id", "node_id", "provider_id", "quota_group",
    "model_id", "lease_issuer", "lease_node_id", "lease_epoch",
    "lease_expires_ms", "now_ms", "quota_reserved", "quota_used",
    "provider_http_status", "trace_capture_ready",
    "independent_archive_ack", "replayed",
)
ALLOWED_PROVIDER_CODES = {200, 401, 402, 403, 429, 503}


def evaluate_mock(request: Mapping[str, Any]) -> dict[str, Any]:
    """Return redacted denial, never a usable capability, process, or permit."""
    if not isinstance(request, Mapping):
        request = {}
    safe_ids = {}
    for field in REQUIRED[:10]:
        val = request.get(field)
        safe_ids[field] = val if isinstance(val, str) and SAFE.fullmatch(val) else None
    reasons: list[str] = []
    if any(value is None for value in safe_ids.values()):
        reasons.append("identity_missing_or_invalid")
    if any(field not in request for field in REQUIRED):
        reasons.append("missing_required_field")
    ints = ("lease_epoch", "lease_expires_ms", "now_ms",
            "quota_reserved", "quota_used", "provider_http_status")
    if any(type(request.get(field)) is not int for field in ints):
        reasons.append("invalid_numeric_type")
    elif request["lease_epoch"] <= 0 or request["now_ms"] < 0:
        reasons.append("invalid_fencing_epoch")
    else:
        if request["lease_expires_ms"] <= request["now_ms"]:
            reasons.append("stale_or_expired_lease")
        if request["quota_used"] < 0 or request["quota_reserved"] < 0:
            reasons.append("invalid_quota")
        elif request["quota_reserved"] <= request["quota_used"]:
            reasons.append("quota_exhausted")
        if request["provider_http_status"] not in ALLOWED_PROVIDER_CODES:
            reasons.append("unrecognized_provider_status")
        if request["provider_http_status"] in {401, 402, 403}:
            reasons.append("access_denied")
        if request["provider_http_status"] in {429, 503}:
            reasons.append("provider_circuit_open")
    if safe_ids.get("node_id") != safe_ids.get("lease_node_id"):
        reasons.append("wrong_node")
    if type(request.get("replayed")) is not bool or request.get("replayed"):
        reasons.append("replay_or_unverified_nonce")
    if request.get("trace_capture_ready") is not True:
        reasons.append("trace_capture_unavailable")
    if request.get("independent_archive_ack") is not True:
        reasons.append("no_independent_custody")
    if not safe_ids.get("lease_issuer"):
        reasons.append("untrusted_issuer")
    # This mock cannot independently validate any of the affirmative claims.
    # Therefore DENY is unconditional, even when the input appears healthy.
    reasons.append("mock_no_authenticated_quota_or_dispatch_authority")
    record = {
        "schema": CONTRACT,
        "decision": "DENY",
        "dispatch_authorized": False,
        "provider_calls": 0,
        "production_execution_commands": 0,
        "identifiers": safe_ids,
        "lease_epoch_observed": request.get("lease_epoch")
            if type(request.get("lease_epoch")) is int else None,
        "reasons": sorted(set(reasons)),
        "quota_authority": "UNVERIFIED",
        "trace_custody": "UNATTESTED",
    }
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    record["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
    return record


def good_synthetic_request() -> dict[str, Any]:
    """Safe unit-test fixture, never a live permit."""
    return {
        "root_run_id": "mock-root-01", "task_id": "mock-task-01",
        "action_id": "mock-action-01", "parent_action_id": "mock-parent-00",
        "worker_id": "mercury-mock-01", "node_id": "xwing",
        "provider_id": "kilo_free", "quota_group": "KILO_UPSTREAM_UNKNOWN",
        "model_id": "mock-model-free", "lease_issuer": "untrusted-test-issuer",
        "lease_node_id": "xwing", "lease_epoch": 1,
        "lease_expires_ms": 5000, "now_ms": 1000,
        "quota_reserved": 1, "quota_used": 0, "provider_http_status": 200,
        "trace_capture_ready": True, "independent_archive_ack": True,
        "replayed": False,
    }


if __name__ == "__main__":
    print(json.dumps(evaluate_mock(good_synthetic_request()), indent=2, sort_keys=True))
