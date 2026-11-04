"""Read-only, privacy-preserving subject-linkage check for synthetic or private receipts.

Stacks on the v1 fail-closed disposition gate; never authorizes a migration.
All evidence is owner-private, and the checksum/timestamp can be changed by the
same administrator: neither proves independent custody or snapshot isolation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone

if __package__:
    from . import endpoint_identity_disposition_gate as base
else:
    import endpoint_identity_disposition_gate as base

MAX_AGE_SECONDS = 3600
MAX_FUTURE_SKEW_SECONDS = 60
SUBJECT_FIELDS = {
    "schema",
    "observed_utc",
    "method",
    "result",
    "container_id_sha256",
    "image_id_sha256",
    "operator_controlled_read_only_evidence",
    "independent_operator_verified",
    "subject_values_or_ids_returned",
    "production_mutated",
    "production_no_go",
}
RESULT_FIELDS = {
    "schema",
    "status",
    "read_only_requested",
    "values_returned",
    "identifiers_returned",
    "nodes",
    "counts",
}
COUNTER_FIELDS = {"owners_a", "owners_b", "shared_owners", "serves_a", "serves_b"}
METHOD = (
    "one fixed Cypher READ_ACCESS statement, explicit rollback, pinned running image"
)


def timestamp_in_recent_window(iso_time: object, now: datetime | None) -> None:
    base.check(
        type(iso_time) is str and 0 < len(iso_time) <= 40, "ONE_QUERY_TIME_MALFORMED"
    )
    try:
        parsed = datetime.fromisoformat(iso_time)
    except ValueError:
        raise base.EvidenceDrift("ONE_QUERY_TIME_MALFORMED") from None
    base.check(parsed.tzinfo is not None, "ONE_QUERY_TIME_UNZONED")
    clock = now if now is not None else datetime.now(timezone.utc)
    base.check(clock.tzinfo is not None, "CHECKER_TIME_UNZONED")
    age = (clock - parsed).total_seconds()
    # A bounded local-clock screen only. The operator controls timestamps and
    # host clock. NO external clock trust, highwater or atomic snapshot claim.
    base.check(
        -MAX_FUTURE_SKEW_SECONDS <= age <= MAX_AGE_SECONDS,
        "ONE_QUERY_RECEIPT_TOO_OLD_OR_FUTURE",
    )


def assess_with_one_query(
    packet: dict,
    shape: dict,
    overlap: dict,
    single: dict,
    *,
    shape_bytes: bytes,
    overlap_bytes: bytes,
    single_bytes: bytes,
    expected_single_sha256: str,
    now: datetime | None = None,
) -> dict:
    """Cross-compare all four documents; valid receipts remain HOLD."""
    old = base.assess(packet, shape, overlap, shape_bytes, overlap_bytes)
    base.check(
        old["status"] == "HOLD_REVIEW_REQUIRED"
        and old["production_release_authorized"] is False,
        "BASELINE_REVIEW_NOT_HOLD",
    )
    base.check(
        type(expected_single_sha256) is str
        and re.fullmatch(r"[0-9a-f]{64}", expected_single_sha256) is not None
        and hashlib.sha256(single_bytes).hexdigest() == expected_single_sha256,
        "ONE_QUERY_EXPECTED_DIGEST_MISMATCH",
    )
    base.check(
        type(single) is dict and set(single) == SUBJECT_FIELDS,
        "ONE_QUERY_RECEIPT_SCHEMA",
    )
    base.check(
        single["schema"] == "assistx.single-transaction-local-coherence-receipt.v1"
        and single["method"] == METHOD
        and single["operator_controlled_read_only_evidence"] is True
        and single["independent_operator_verified"] is False
        and single["subject_values_or_ids_returned"] is False
        and single["production_mutated"] is False
        and single["production_no_go"] is True,
        "ONE_QUERY_PROVENANCE_NOT_DENYING_RELEASE",
    )
    for key in ("container_id_sha256", "image_id_sha256"):
        base.check(
            type(single[key]) is str
            and re.fullmatch(r"[0-9a-f]{64}", single[key]) is not None,
            "ONE_QUERY_CONTAINER_DIGEST_MALFORMED",
        )
    timestamp_in_recent_window(single["observed_utc"], now)
    result = single["result"]
    base.check(
        type(result) is dict
        and set(result) == RESULT_FIELDS
        and result["schema"] == "assistx.one-query-endpoint-collision-census.v1"
        and result["status"] == "READ_ONLY_SINGLE_STATEMENT_PASS"
        and result["read_only_requested"] is True
        and result["identifiers_returned"] is False
        and result["values_returned"] is False,
        "ONE_QUERY_RESULT_NOT_READONLY",
    )
    nodes = result["nodes"]
    base.check(
        type(nodes) is list and len(nodes) == 2, "ONE_QUERY_DUPLICATE_COUNT_DRIFT"
    )
    single_shapes = sorted(
        (base.record_shape(x, source=True) for x in nodes), key=lambda s: len(s[1])
    )
    original_shapes = sorted(
        (base.record_shape(x, source=True) for x in shape["result"]["nodes"]),
        key=lambda s: len(s[1]),
    )
    base.check(
        single_shapes == original_shapes, "ONE_QUERY_SUBJECT_OR_PROPERTY_MISMATCH"
    )
    counts = result["counts"]
    previous = overlap["result"]["counts"]
    base.check(
        type(counts) is dict
        and set(counts) == COUNTER_FIELDS
        and all(type(v) is int for v in counts.values()),
        "ONE_QUERY_OWNER_COUNTER_SCHEMA",
    )
    base.check(
        sorted((counts["owners_a"], counts["owners_b"]))
        == sorted((previous["owners_a"], previous["owners_b"]))
        and counts["shared_owners"] == previous["shared_owners"]
        and sorted((counts["serves_a"], counts["serves_b"]))
        == sorted((previous["serves_a"], previous["serves_b"])),
        "ONE_QUERY_OWNER_COUNTER_MISMATCH",
    )
    return {
        "status": "HOLD_ONE_QUERY_CONSISTENT_BUT_UNAPPROVED",
        "node_count": 2,
        "property_counts": [9, 17],
        "relationship_count": 3,
        "one_query_subject_shape_matches_prior_census": True,
        "serializable_snapshot_isolation_verified": False,
        "outside_operator_attestation_verified": False,
        "independent_custody_verified": False,
        "production_release_authorized": False,
        "write_actions_executed": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Four owner-private receipts; never GO"
    )
    parser.add_argument("--private-review")
    parser.add_argument("--private-shape")
    parser.add_argument("--private-owners")
    parser.add_argument("--private-one-query")
    parser.add_argument("--expected-one-query-sha256")
    args = parser.parse_args(argv)
    required = (
        args.private_review,
        args.private_shape,
        args.private_owners,
        args.private_one_query,
        args.expected_one_query_sha256,
    )
    if not all(required):
        print(
            json.dumps(
                {
                    "status": "HOLD_FOUR_PRIVATE_EVIDENCE_INPUTS_REQUIRED",
                    "production_release_authorized": False,
                }
            )
        )
        return 2
    try:
        review_bytes = base.private_read(args.private_review)
        shape_bytes = base.private_read(args.private_shape)
        owner_bytes = base.private_read(args.private_owners)
        one_bytes = base.private_read(args.private_one_query)
        decision = assess_with_one_query(
            base.decode(review_bytes),
            base.decode(shape_bytes),
            base.decode(owner_bytes),
            base.decode(one_bytes),
            shape_bytes=shape_bytes,
            overlap_bytes=owner_bytes,
            single_bytes=one_bytes,
            expected_single_sha256=args.expected_one_query_sha256,
        )
    except base.EvidenceDrift as exc:
        print(
            json.dumps(
                {
                    "status": "DENY_EVIDENCE_DRIFT",
                    "reason": str(exc),
                    "production_release_authorized": False,
                }
            )
        )
        return 3
    print(json.dumps(decision, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
