#!/usr/bin/env python3
"""Read-only, image-pinned native Neo4j authority census across three containers.

Research-only. Invokes the existing hardened native privileges probe, one
allowlisted RUNNING container at a time, with an immutable SHA256 image
pin for each. Does NOT create/revoke users, grant/deny privileges, mutate
graphs or access credentials outside the existing containers.

Every result is HOLD, including apparent least-privilege: no independent
attestation of physical ingress, writer enrollment or anti-rollback custody
is established by privileges alone.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

# Import via the repository namespace when tests load this as a module;
# direct execution from scripts/ also works without editable installs.
try:
    from scripts import read_only_native_graph_privileges as native
except ModuleNotFoundError:
    import read_only_native_graph_privileges as native


TARGETS = (
    ("assistx-api", "api"),
    ("assistx-worker", "worker"),
    ("assistx-hermes-adapter", "hermes"),
)
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


class CensusHold(RuntimeError):
    """Any missing/ambiguous/changed native evidence refuses the gate."""


def census(*, pinned_images: dict[str, str]) -> dict[str, Any]:
    if set(pinned_images) != {name for name, _ in TARGETS}:
        raise CensusHold("all_allowlisted_runtime_image_pins_required")
    if not all(isinstance(value, str) and IMAGE_ID.fullmatch(value)
               for value in pinned_images.values()):
        raise CensusHold("invalid_or_missing_immutable_image_pin")

    results = []
    # native.collect takes a single fixed global TARGET. Safe ONLY because
    # this executable is sequential and cannot accept arbitrary targets.
    # Always restore its value, even when a container fails its identity check.
    original_target = native.TARGET
    try:
        for target, _label in TARGETS:
            native.TARGET = target
            try:
                receipt = native.collect(expected_image=pinned_images[target])
            except native.CensusHold as exc:
                raise CensusHold("pinned_native_readback_failed") from exc
            # No usernames, roles list, query rows or secret env are exposed.
            expected = {
                "evidence_tier","container_id","image_id",
                "native_query_scope","granted_privilege_count",
                "wildcard_graph_grant_count","write_action_count",
                "role_count","has_admin_role","no_mutations",
                "production_decision","promotion_authorized",
                "effective_privilege_cutover_verified",
            }
            if (set(receipt) != expected
                or receipt["production_decision"] != "HOLD"
                or receipt["promotion_authorized"] is not False
                or receipt["effective_privilege_cutover_verified"] is not False
                or receipt["no_mutations"] is not True
                or receipt["image_id"] != pinned_images[target]):
                raise CensusHold("unexpected_native_evidence_envelope")
            results.append({
                "service":target,
                "image_pinned":True,
                "role_count":receipt["role_count"],
                "has_admin_role":receipt["has_admin_role"],
                "grant_count":receipt["granted_privilege_count"],
                "wildcard_graph_grants":receipt["wildcard_graph_grant_count"],
                "write_action_grants":receipt["write_action_count"],
            })
    finally:
        native.TARGET = original_target

    return {
        "production_decision":"HOLD",
        "read_only_native_evidence":True,
        "all_required_services_inspected":len(results)==len(TARGETS),
        "roles_cutover_verified":False,
        "global_task_writer_authority_verified":False,
        "dispatch_authorized":False,
        "broad_admin_service_count":sum(r["has_admin_role"] for r in results),
        "broad_write_service_count":sum(r["write_action_grants"]>0 for r in results),
        "wildcard_graph_service_count":sum(r["wildcard_graph_grants"]>0 for r in results),
        "services":results,
    }


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-api-image",required=True)
    parser.add_argument("--expected-worker-image",required=True)
    parser.add_argument("--expected-hermes-image",required=True)
    parser.add_argument("--json",action="store_true",required=True)
    args=parser.parse_args(argv)
    pins={
        "assistx-api":args.expected_api_image,
        "assistx-worker":args.expected_worker_image,
        "assistx-hermes-adapter":args.expected_hermes_image,
    }
    try:
        result=census(pinned_images=pins)
    except CensusHold:
        print(json.dumps({
            "production_decision":"HOLD","dispatch_authorized":False,
            "all_required_services_inspected":False,
            "error_type":"pinned_native_census_unverified",
        },sort_keys=True))
        return 3
    print(json.dumps(result,sort_keys=True))
    # An explicit failing status for broad production roles. No output status
    # is a production GO, including 0 from a future fully restricted survey.
    return 3 if result["broad_admin_service_count"] or result["broad_write_service_count"] or result["wildcard_graph_service_count"] else 0


if __name__=="__main__":
    sys.exit(main())
