"""Offline Hermes census-page envelope validator — HOLD-only research.

Checks ordering, completeness and a supplied signature verifier's responses.
No production API, signer, source-generation writer fence or independently
administered high-water is provided by this module. A caller-controlled
verifier/floor does NOT establish independent custody or production authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

OWNER = "hermes-local"
PAGE_SIZE = 500
MAX_RECORDS = 10000
DOMAIN = b"assistx-hermes-census-pages-research-v1\0"
IDENTITY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
HEX = re.compile(r"^[a-f0-9]{64}$")
STATES = frozenset(("CLAIMED", "RUNNING", "PAUSING", "READY",
                    "DONE", "FAILED", "CANCELLED"))
RECORD_FIELDS = frozenset((
    "task_id", "claimed_by", "target_agent_id", "claim_id",
    "session_id", "attempt", "lease_expiry", "status",
    "revocation_state", "resource_effect_ref",
))
PAGE_FIELDS = frozenset((
    "schema", "owner", "generation", "snapshot_id", "issued_at_ms",
    "expires_at_ms", "page_number", "page_count", "total",
    "previous_task_id", "last_task_id", "previous_page_sha256",
    "snapshot_digest_sha256", "records", "signature",
))


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def digest(value: Any) -> str:
    return hashlib.sha256(DOMAIN + canonical(value)).hexdigest()


def payload(page: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in page.items() if k != "signature"}


def _valid_record(rec: Any) -> bool:
    if not isinstance(rec, dict) or set(rec) != RECORD_FIELDS:
        return False
    if (not isinstance(rec["task_id"], str)
            or not IDENTITY.fullmatch(rec["task_id"])
            or rec["claimed_by"] != OWNER
            or rec["status"] not in STATES):
        return False
    for field in ("claim_id", "session_id"):
        if not isinstance(rec[field], str) or not IDENTITY.fullmatch(rec[field]):
            return False
    if rec["target_agent_id"] not in (None, OWNER):
        return False
    if (type(rec["attempt"]) is not int or rec["attempt"] < 1
            or type(rec["lease_expiry"]) is not int or rec["lease_expiry"] < 0):
        return False
    if rec["revocation_state"] not in ("unverified", "pending", "revoked"):
        return False
    ref = rec["resource_effect_ref"]
    return ref == "unavailable" or (
        isinstance(ref, str) and bool(IDENTITY.fullmatch(ref))
    )


def _validate(
    pages: Any,
    *,
    now_ms: int,
    floor: Any,
    signature_verifier: Callable[[bytes, str], bool],
) -> tuple[int, str]:
    if (not isinstance(pages, list) or not 1 <= len(pages) <= 20
            or type(now_ms) is not int or now_ms < 0
            or not callable(signature_verifier)
            or not isinstance(floor, dict)
            or set(floor) != {"owner", "accepted_generation"}
            or floor["owner"] != OWNER
            or type(floor["accepted_generation"]) is not int
            or floor["accepted_generation"] < 0):
        raise ValueError("invalid_census_envelope")
    first = pages[0]
    if not isinstance(first, dict) or set(first) != PAGE_FIELDS:
        raise ValueError("invalid_census_envelope")
    total = first["total"]
    generation = first["generation"]
    if (type(total) is not int or not 0 <= total <= MAX_RECORDS
            or type(generation) is not int
            or generation <= floor["accepted_generation"]):
        raise ValueError("invalid_or_replayed_generation")
    count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if len(pages) != count:
        raise ValueError("census_missing_or_extra_pages")
    boundary = (
        "schema", "owner", "generation", "snapshot_id", "issued_at_ms",
        "expires_at_ms", "page_count", "total", "snapshot_digest_sha256",
    )
    if (type(first["schema"]) is not int or first["schema"] != 1
            or first["owner"] != OWNER
            or not isinstance(first["snapshot_id"], str)
            or not IDENTITY.fullmatch(first["snapshot_id"])
            or type(first["issued_at_ms"]) is not int
            or type(first["expires_at_ms"]) is not int
            or first["issued_at_ms"] > now_ms
            or first["expires_at_ms"] <= now_ms
            or first["expires_at_ms"] - first["issued_at_ms"] > 300000
            or type(first["page_count"]) is not int
            or first["page_count"] != count
            or not isinstance(first["snapshot_digest_sha256"], str)
            or not HEX.fullmatch(first["snapshot_digest_sha256"])):
        raise ValueError("invalid_or_expired_snapshot")
    complete: list[dict[str, Any]] = []
    prior_task: str | None = None
    prior_page_sha: str | None = None
    for i, page in enumerate(pages):
        if not isinstance(page, dict) or set(page) != PAGE_FIELDS:
            raise ValueError("malformed_census_page")
        if (any(page[field] != first[field] for field in boundary)
                or type(page["page_number"]) is not int
                or page["page_number"] != i
                or page["previous_task_id"] != prior_task
                or page["previous_page_sha256"] != prior_page_sha):
            raise ValueError("census_cursor_or_generation_mismatch")
        records = page["records"]
        needed = min(PAGE_SIZE, total - i * PAGE_SIZE)
        if (not isinstance(records, list)
                or len(records) != needed
                or not all(_valid_record(r) for r in records)):
            raise ValueError("invalid_census_page_records")
        for rec in records:
            task = rec["task_id"]
            if prior_task is not None and task <= prior_task:
                raise ValueError("duplicate_or_out_of_order_task")
            prior_task = task
        if page["last_task_id"] != prior_task:
            raise ValueError("census_cursor_mismatch")
        signature = page["signature"]
        if not isinstance(signature, str) or not signature:
            raise ValueError("missing_signature")
        content = payload(page)
        try:
            verified = signature_verifier(DOMAIN + canonical(content), signature)
        except Exception as exc:
            raise ValueError("signature_verifier_failed") from exc
        if verified is not True:
            raise ValueError("invalid_signature")
        prior_page_sha = digest(content)
        complete.extend(records)
    if len(complete) != total or digest(complete) != first["snapshot_digest_sha256"]:
        raise ValueError("inconsistent_snapshot_digest")
    return total, first["snapshot_digest_sha256"]


def verify_candidate_pages(
    pages: Any,
    *,
    now_ms: int,
    external_floor: Any,
    signature_verifier: Callable[[bytes, str], bool],
) -> dict[str, Any]:
    """Validate syntax and caller-supplied callbacks; NEVER authorize release."""
    count = None
    candidate_digest = None
    consistent = False
    reason = "unverified_external_generation_and_custody"
    try:
        count, candidate_digest = _validate(
            pages, now_ms=now_ms, floor=external_floor,
            signature_verifier=signature_verifier,
        )
        consistent = True
    except Exception:
        # Do not leak claim IDs, task data, signatures or a signer traceback.
        reason = "census_page_signature_cursor_or_completeness_rejected"
    return {
        "schema": 1, "mode": "hermes_offline_page_contract_research",
        "owner": OWNER, "candidate_pages_consistent": consistent,
        "candidate_count": count,
        "candidate_digest_sha256": candidate_digest,
        "reason": reason, "source_generation_fenced": False,
        "externally_anchored_floor_verified": False,
        "independent_key_custody_verified": False,
        "resource_side_effects_verified": False,
        "production_dispatch_authorized": False,
        "decision": "HOLD",
    }
