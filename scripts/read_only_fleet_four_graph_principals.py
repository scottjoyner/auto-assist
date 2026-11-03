"""Four-service native graph authority gate: AssistX API/worker/Hermes + PhoneLog.

Research-only fixed targets. Reuses approved read-only inspections, exact
running-image pins and only publishes sanitized grant counts. Never permits
deployment, credential rotation, graph writes or Task dispatch.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

import read_only_fleet_graph_principals as three
import read_only_phonelog_graph_privileges as phone

_NAMES = ("assistx-api", "assistx-worker", "assistx-hermes-adapter", "phonelog")
_SHA = re.compile(r"sha256:[a-f0-9]{64}\Z")


class FourPrincipalHold(RuntimeError):
    """Missing, contradictory or untrusted runtime evidence."""


def census(*, pinned_images: dict[str, str]) -> dict[str, Any]:
    if (
        type(pinned_images) is not dict
        or set(pinned_images) != set(_NAMES)
        or not all(
            type(value) is str and _SHA.fullmatch(value)
            for value in pinned_images.values()
        )
    ):
        raise FourPrincipalHold("all_four_immutable_image_pins_required")
    try:
        first = three.census(
            pinned_images={name: pinned_images[name] for name in _NAMES[:3]}
        )
        fourth = phone.collect(expected_image=pinned_images["phonelog"])
    except (three.CensusHold, phone.PhoneCensusHold):
        raise FourPrincipalHold("native_image_pinned_census_unverified") from None
    # Source-level trust boundary: old three-service report must not become
    # an authorization just because one new native client responded.
    expected_base = {
        "production_decision", "read_only_native_evidence",
        "all_required_services_inspected", "roles_cutover_verified",
        "global_task_writer_authority_verified", "dispatch_authorized",
        "broad_admin_service_count", "broad_write_service_count",
        "wildcard_graph_service_count", "services",
    }
    if (
        type(first) is not dict
        or set(first) != expected_base
        or first["production_decision"] != "HOLD"
        or first["dispatch_authorized"] is not False
        or first["roles_cutover_verified"] is not False
        or first["global_task_writer_authority_verified"] is not False
        or first["read_only_native_evidence"] is not True
        or first["all_required_services_inspected"] is not True
        or type(first["services"]) is not list
        or len(first["services"]) != 3
        or [x.get("service") for x in first["services"]] != list(_NAMES[:3])
    ):
        raise FourPrincipalHold("untrusted_three_service_readback")
    expected_phone = {
        "evidence_tier", "container_id", "image_id", "native_query_scope",
        "granted_privilege_count", "wildcard_graph_grant_count",
        "write_action_count", "role_count", "has_admin_role", "no_mutations",
        "production_decision", "promotion_authorized",
        "effective_privilege_cutover_verified",
    }
    if (
        type(fourth) is not dict or set(fourth) != expected_phone
        or fourth["image_id"] != pinned_images["phonelog"]
        or fourth["evidence_tier"] != "PRODUCTION_READ_ONLY_NATIVE_NEO4J"
        or fourth["native_query_scope"] != "CURRENT_USER_PRIVILEGES_READ_ONLY"
        or fourth["no_mutations"] is not True
        or fourth["production_decision"] != "HOLD"
        or fourth["promotion_authorized"] is not False
        or fourth["effective_privilege_cutover_verified"] is not False
    ):
        raise FourPrincipalHold("untrusted_phonelog_readback")
    phone_counts = {
        "has_admin_role": fourth["has_admin_role"],
        "role_count": fourth["role_count"],
        "grant_count": fourth["granted_privilege_count"],
        "wildcard_graph_grants": fourth["wildcard_graph_grant_count"],
        "write_action_grants": fourth["write_action_count"],
    }
    if (
        type(phone_counts["has_admin_role"]) is not bool
        or any(
            type(phone_counts[k]) is not int or not 0 <= phone_counts[k] <= 10000
            for k in ("role_count", "grant_count", "wildcard_graph_grants", "write_action_grants")
        )
        or phone_counts["wildcard_graph_grants"] > phone_counts["grant_count"]
        or phone_counts["write_action_grants"] > phone_counts["grant_count"]
    ):
        raise FourPrincipalHold("untrusted_phonelog_counts")
    services = list(first["services"]) + [{
        "service": "phonelog",
        "image_pinned": True,
        **phone_counts,
    }]
    if any(
        type(x) is not dict or set(x) != {
            "service", "image_pinned", "role_count", "has_admin_role",
            "grant_count", "wildcard_graph_grants", "write_action_grants",
        }
        or x["image_pinned"] is not True
        or type(x["has_admin_role"]) is not bool
        or any(
            type(x[k]) is not int or not 0 <= x[k] <= 10000
            for k in ("role_count", "grant_count", "wildcard_graph_grants", "write_action_grants")
        )
        for x in services
    ):
        raise FourPrincipalHold("invalid_four_service_envelope")
    return {
        "production_decision": "HOLD",
        "dispatch_authorized": False,
        "all_required_services_inspected": True,
        "read_only_native_evidence": True,
        "roles_cutover_verified": False,
        "global_task_writer_authority_verified": False,
        "service_count": 4,
        "broad_admin_service_count": sum(x["has_admin_role"] for x in services),
        "broad_write_service_count": sum(x["write_action_grants"] > 0 for x in services),
        "wildcard_graph_service_count": sum(x["wildcard_graph_grants"] > 0 for x in services),
        "services": services,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("api", "worker", "hermes", "phonelog"):
        ap.add_argument(f"--expected-{name}-image", required=True)
    ap.add_argument("--json", action="store_true", required=True)
    args = ap.parse_args(argv)
    pins = {
        "assistx-api": args.expected_api_image,
        "assistx-worker": args.expected_worker_image,
        "assistx-hermes-adapter": args.expected_hermes_image,
        "phonelog": args.expected_phonelog_image,
    }
    try:
        result = census(pinned_images=pins)
    except FourPrincipalHold:
        result = {
            "production_decision": "HOLD",
            "dispatch_authorized": False,
            "all_required_services_inspected": False,
            "error_type": "four_service_native_census_unverified",
        }
    print(json.dumps(result, sort_keys=True))
    # Every result, even a synthetic all-reader result, remains a non-GO
    # pending independent physical writer/custodian evidence.
    return 3


if __name__ == "__main__":
    sys.exit(main())
