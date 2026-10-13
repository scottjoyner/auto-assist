#!/usr/bin/env python3
"""Review-only preflight for *fresh* runtime-projection reattestation evidence.

Unsigned evidence cannot approve or mint a canonical runtime projection.
No Neo4j, HTTP, SSH, file mutation, expiration extension, provider invocation,
credential access, approval, or dispatch occurs in this module.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = "runtime-projection-review-preflight-v1"
FIELDS = {
    "schema", "kind", "prior_generation", "proposed_generation",
    "previous_approval_expired", "expected_model_sha256",
    "expected_model_bytes", "physical", "paths", "capacity",
    "operator_review",
}
PHYSICAL = {
    "observed_at_utc", "model_sha256", "model_bytes", "loaded_model_count",
    "matching_process_count", "context_tokens", "pid", "unsigned_host_source",
}
PATH = {"transport", "observed_at_utc", "http", "exact_marker", "max_tokens"}
CAPACITY = {"requested_slots", "physical_process_parallel"}
REVIEW = {"new_approval_id", "reviewer_id", "independent_host_signature"}
SHA = re.compile(r"[a-f0-9]{64}\Z")
PHYSICAL_TTL = timedelta(minutes=5)
PATH_TTL = timedelta(minutes=3)


def exact(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(label + " unexpected/missing fields")
    return value


def count(value: object, name: str, maximum: int = 1_000_000_000) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("invalid " + name)
    return value


def timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise ValueError("invalid " + name)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid " + name) from exc


def age_blockers(
    observed: datetime, now: datetime, ttl: timedelta, label: str,
) -> list[str]:
    if observed > now + timedelta(seconds=5):
        return [label + "_FUTURE_DATED"]
    if now - observed > ttl:
        return [label + "_STALE"]
    return []


def inspect(evidence: object, *, now: datetime | None = None) -> dict[str, Any]:
    """Validate a proposed operator review, NEVER grant permission to apply."""
    e = exact(evidence, FIELDS, "review envelope")
    if e["schema"] != SCHEMA or e["kind"] != "unsigned_read_only_observation":
        raise ValueError("not an admissible review-only envelope")
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("inspection clock must be UTC-aware")
    previous = count(e["prior_generation"], "prior_generation")
    proposed = count(e["proposed_generation"], "proposed_generation")
    if proposed != previous + 1:
        raise ValueError("generation must advance exactly one from observed canonical")
    if type(e["previous_approval_expired"]) is not bool:
        raise ValueError("invalid previous_approval_expired")
    digest = e["expected_model_sha256"]
    if not isinstance(digest, str) or not SHA.fullmatch(digest):
        raise ValueError("invalid expected GGUF digest")
    size = count(e["expected_model_bytes"], "expected_model_bytes", 2**31)
    if size == 0:
        raise ValueError("empty model cannot be approved")

    physical = exact(e["physical"], PHYSICAL, "physical")
    actual = timestamp(physical["observed_at_utc"], "physical observation")
    count(physical["model_bytes"], "physical model bytes", 2**31)
    count(physical["matching_process_count"], "matching process count", 32)
    count(physical["loaded_model_count"], "loaded model count", 256)
    count(physical["context_tokens"], "context tokens", 1_048_576)
    count(physical["pid"], "physical PID")
    if type(physical["unsigned_host_source"]) is not bool:
        raise ValueError("invalid host source flag")

    paths = e["paths"]
    if not isinstance(paths, list) or len(paths) != 2:
        raise ValueError("exactly two direct private-path observations required")
    transports: set[str] = set()
    blocked: list[str] = age_blockers(actual, now, PHYSICAL_TTL, "PHYSICAL")
    for item in paths:
        p = exact(item, PATH, "path")
        transport = p["transport"]
        if transport not in ("lan", "tailscale") or transport in transports:
            raise ValueError("require distinct LAN and Tailscale paths")
        transports.add(transport)
        blocked += age_blockers(
            timestamp(p["observed_at_utc"], "path observation"), now,
            PATH_TTL, transport.upper() + "_PATH",
        )
        if count(p["http"], "path HTTP", 599) != 200 or (
            type(p["exact_marker"]) is not bool or not p["exact_marker"]
        ):
            blocked.append(transport.upper() + "_DIRECT_COMPLETION_NOT_PROVEN")
        if count(p["max_tokens"], "path token budget", 128) > 24:
            blocked.append(transport.upper() + "_CANARY_BUDGET_TOO_LARGE")
    if not {"lan", "tailscale"} == transports:
        raise ValueError("both private paths are required")
    if (physical["model_sha256"] != digest or physical["model_bytes"] != size
            or physical["matching_process_count"] != 1
            or physical["loaded_model_count"] != 1
            or physical["context_tokens"] <= 0 or physical["pid"] <= 0):
        blocked.append("PHYSICAL_MODEL_IDENTITY_UNPROVEN")
    if not physical["unsigned_host_source"]:
        raise ValueError("cannot claim a host signature in unsigned evidence")

    cap = exact(e["capacity"], CAPACITY, "capacity")
    slots = count(cap["requested_slots"], "requested_slots", 256)
    parallel = count(cap["physical_process_parallel"], "physical parallelism", 256)
    if not 1 <= slots <= parallel:
        blocked.append("CAPACITY_EXCEEDS_OBSERVED_CONFIGURATION")
    # Source-approved capacity may be lower than the physical process parallelism;
    # this check does not independently verify allocator slots or owner leasing.
    review = exact(e["operator_review"], REVIEW, "operator_review")
    if review["new_approval_id"] is not None or review["reviewer_id"] is not None:
        raise ValueError("unsigned envelope cannot carry approval credentials")
    if review["independent_host_signature"] is not False:
        raise ValueError("unsigned host data cannot claim independent signing")
    if not e["previous_approval_expired"]:
        blocked.append("PREVIOUS_APPROVAL_STATE_NOT_EXPIRED")
    blocked.append("NEW_EXPLICIT_OPERATOR_APPROVAL_REQUIRED")
    blocked.append("CANONICAL_GENERATION_NOT_INDEPENDENTLY_ATTESTED")
    blocked.append("TRUSTED_SOURCE_AND_PER_USER_RECEIPTS_OUTSTANDING")

    return {
        "schema": "runtime-projection-review-result-v1",
        "observed_generation": previous,
        "proposed_generation_for_review_only": proposed,
        "evaluated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "physical_and_path_evidence_fresh": not any(
            name.endswith(("_STALE", "_FUTURE_DATED")) for name in blocked
        ),
        "blockers": sorted(set(blocked)),
        "status": "REVIEW_BLOCKED",
        "input_kind": "unsigned_read_only_observation",
        "operator_approval_minted": False,
        "new_canonical_generation_created": False,
        "can_auto_renew": False,
        "can_apply": False,
        "can_dispatch": False,
        "source_attested": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review_envelope", type=Path)
    args = parser.parse_args()
    try:
        path = args.review_envelope
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 8192:
            raise ValueError("expected small regular read-only review envelope")
        obj = json.loads(path.read_text(encoding="utf-8"))
        result = inspect(obj)
    except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print("review preflight refused: " + type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
