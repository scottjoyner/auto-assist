"""Offline, read-only value-free receipt cross-check. Always HOLD or DENY.

No DB/network/Docker imports, no executable migration instructions.
Never emits identity, property names, values or external paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from collections import Counter

MAX_FILE_BYTES = 131072
REVIEW_KEYS = {
    "schema",
    "created_utc",
    "purpose",
    "notes",
    "records",
    "source_shape_sha256",
    "source_overlap_sha256",
    "live_duplicate_count",
    "shared_owner_nodes",
    "live_identity_values_captured",
    "live_property_values_captured",
    "owner_and_relationship_semantics_approved",
    "property_disposition_approved",
    "consumer_references_fully_censused",
    "exact_rollback_rehearsed_with_real_data_shapes",
    "legacy_writers_quiesced",
    "DBMS_and_cross_database_denial_tested",
    "full_restricted_role_cutover_tested",
    "external_custody_verified",
    "operational_execution_allowed",
    "graph_admission",
    "production_no_go",
}
FALSE_FLAGS = {
    "live_identity_values_captured",
    "live_property_values_captured",
    "owner_and_relationship_semantics_approved",
    "property_disposition_approved",
    "consumer_references_fully_censused",
    "exact_rollback_rehearsed_with_real_data_shapes",
    "legacy_writers_quiesced",
    "DBMS_and_cross_database_denial_tested",
    "full_restricted_role_cutover_tested",
    "external_custody_verified",
    "operational_execution_allowed",
}
RECORD_KEYS = {
    "record_alias",
    "labels",
    "property_count",
    "properties",
    "relationships",
    "survivor_selection",
}
PROP_KEYS = {
    "key_name",
    "current_value",
    "disposition",
    "owner_approval_digest",
    "consumer_dependency_reviewed",
}
REL_KEYS = {
    "type",
    "direction",
    "neighbor_labels",
    "relationship_property_keys",
    "referent_identity",
    "migration_disposition",
}
SOURCE_REL_KEYS = {"type", "direction", "neighbor_labels", "relation_property_keys"}


class EvidenceDrift(ValueError):
    """Safe fixed reason code, never raw private content."""


def check(predicate, reason):
    if not predicate:
        raise EvidenceDrift(reason)


def duplicate_denial(pairs):
    out = {}
    for key, value in pairs:
        check(key not in out, "DUPLICATE_JSON_KEY")
        out[key] = value
    return out


def private_read(path):
    """Do not follow symlinks, reject group/world-readable or oversized inputs."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise EvidenceDrift("PRIVATE_FILE_UNAVAILABLE") from None
    try:
        info = os.fstat(fd)
        check(stat.S_ISREG(info.st_mode), "NOT_REGULAR_FILE")
        check(
            info.st_uid == os.geteuid() and info.st_nlink == 1,
            "UNTRUSTED_PRIVATE_FILE_OWNER",
        )
        check((stat.S_IMODE(info.st_mode) & 0o077) == 0, "PRIVATE_FILE_PERMISSIONS")
        check(0 < info.st_size <= MAX_FILE_BYTES, "PRIVATE_FILE_SIZE")
        content = os.read(fd, MAX_FILE_BYTES + 1)
        check(len(content) == info.st_size, "PRIVATE_FILE_CHANGED")
        return content
    finally:
        os.close(fd)


def decode(data):
    try:
        obj = json.loads(data.decode("utf-8"), object_pairs_hook=duplicate_denial)
    except EvidenceDrift:
        raise
    except (ValueError, UnicodeError, TypeError):
        raise EvidenceDrift("BAD_JSON") from None
    check(type(obj) is dict, "NOT_OBJECT_JSON")
    return obj


def names(value, *, empty=False):
    check(type(value) is list and len(value) <= 40, "INVALID_NAMES_LIST")
    check(bool(value) or empty, "EMPTY_NAMES_LIST")
    check(
        all(type(x) is str and 0 < len(x) <= 160 and x.isprintable() for x in value),
        "INVALID_NAME",
    )
    check(len(set(value)) == len(value), "DUPLICATE_NAME")
    return tuple(sorted(value))


def signature(edge, *, source):
    check(
        type(edge) is dict and set(edge) == (SOURCE_REL_KEYS if source else REL_KEYS),
        "RELATIONSHIP_SCHEMA_DRIFT",
    )
    check(
        edge["type"] in ("EXPOSES", "SERVES") and edge["direction"] in ("IN", "OUT"),
        "UNKNOWN_RELATIONSHIP",
    )
    if source:
        attributes = edge["relation_property_keys"]
    else:
        check(
            edge["migration_disposition"] == "UNREVIEWED_HOLD"
            and edge["referent_identity"] == "NOT_READ",
            "RELATIONSHIP_UNAUTHORIZED",
        )
        attributes = edge["relationship_property_keys"]
    neighbor = names(edge["neighbor_labels"])
    check(
        neighbor == (("SwarmNode",) if edge["type"] == "EXPOSES" else ("Model",)),
        "UNEXPECTED_NEIGHBOR_LABEL",
    )
    return (edge["type"], edge["direction"], neighbor, names(attributes, empty=True))


def record_shape(record, *, source):
    check(
        type(record) is dict
        and set(record)
        == ({"labels", "property_keys", "edges"} if source else RECORD_KEYS),
        "NODE_SCHEMA_DRIFT",
    )
    labels = names(record["labels"])
    check(
        labels in (("ServiceEndpoint",), ("ModelEndpoint", "ServiceEndpoint")),
        "UNKNOWN_NODE_LABEL",
    )
    if source:
        property_names = names(record["property_keys"])
        edges = record["edges"]
    else:
        check(record["survivor_selection"] == "NOT_AUTHORIZED", "SURVIVOR_SELECTED")
        check(
            type(record["record_alias"]) is str
            and re.fullmatch(r"colliding_shape_[12]", record["record_alias"])
            is not None,
            "UNKNOWN_RECORD_ALIAS",
        )
        props = record["properties"]
        check(type(props) is list and len(props) <= 40, "INVALID_PROPERTY_LIST")
        key_names = []
        for field in props:
            check(
                type(field) is dict and set(field) == PROP_KEYS, "PROPERTY_SCHEMA_DRIFT"
            )
            check(field["current_value"] == "NOT_READ", "PRIVATE_VALUE_INCLUDED")
            check(
                field["disposition"] == "UNREVIEWED_HOLD"
                and field["owner_approval_digest"] is None
                and field["consumer_dependency_reviewed"] is False,
                "PROPERTY_REVIEW_NOT_APPROVED",
            )
            key_names.append(field["key_name"])
        property_names = names(key_names)
        check(
            type(record["property_count"]) is int
            and record["property_count"] == len(property_names),
            "PROPERTY_COUNT_DRIFT",
        )
        edges = record["relationships"]
    check(type(edges) is list and len(edges) <= 10, "INVALID_EDGE_LIST")
    edge_counts = Counter(signature(edge, source=source) for edge in edges)
    types = [(e["type"], e["direction"]) for e in edges]
    check(
        types.count(("EXPOSES", "IN")) == 1
        and types.count(("SERVES", "OUT")) == (1 if len(labels) == 2 else 0)
        and len(types) == (1 if len(labels) == 1 else 2),
        "EDGE_SHAPE_DRIFT",
    )
    return (labels, property_names, edge_counts)


def assess(packet, shape, overlap, shape_raw, overlap_raw):
    """Approve *only* the fidelity of the census, never data migration."""
    check(type(packet) is dict and set(packet) == REVIEW_KEYS, "REVIEW_PACKET_SCHEMA")
    check(
        packet["schema"] == "assistx.endpoint-identity-duplicate-owner-review.v1",
        "REVIEW_PACKET_VERSION",
    )
    check(
        all(type(packet[x]) is str for x in ("created_utc", "purpose", "notes")),
        "INVALID_METADATA",
    )
    check(
        packet["graph_admission"] == "DENY"
        and packet["production_no_go"] is True
        and all(packet[x] is False for x in FALSE_FLAGS),
        "UNAUTHORIZED_RELEASE_CLAIM",
    )
    check(
        type(packet["live_duplicate_count"]) is int
        and packet["live_duplicate_count"] == 2
        and type(packet["shared_owner_nodes"]) is int
        and packet["shared_owner_nodes"] == 1,
        "DUPLICATE_OWNER_COUNT_DRIFT",
    )
    for claim, raw in (
        ("source_shape_sha256", shape_raw),
        ("source_overlap_sha256", overlap_raw),
    ):
        check(
            type(packet[claim]) is str
            and re.fullmatch(r"[0-9a-f]{64}", packet[claim]) is not None
            and hashlib.sha256(raw).hexdigest() == packet[claim],
            "EVIDENCE_HASH_MISMATCH",
        )
    check(
        type(shape) is dict
        and set(shape)
        == {
            "schema",
            "observed_utc",
            "container_image_pinned",
            "live_migration_approved",
            "production_mutated",
            "production_no_go",
            "query_returns_identifiers",
            "query_returns_property_values",
            "result",
        }
        and shape.get("schema") == "assistx.fixed-readonly-duplicate-structure.v1"
        and shape.get("container_image_pinned") is True
        and shape.get("production_mutated") is False
        and shape.get("production_no_go") is True
        and shape.get("query_returns_identifiers") is False
        and shape.get("query_returns_property_values") is False
        and shape.get("live_migration_approved") is False,
        "UNSAFE_SHAPE_PROVENANCE",
    )
    r = shape.get("result")
    check(
        type(r) is dict
        and set(r)
        == {"status", "read_only", "record_values_returned", "node_count", "nodes"}
        and r.get("status") == "READ_ONLY_DUPLICATE_SHAPE_PASS"
        and r.get("read_only") is True
        and r.get("record_values_returned") is False
        and type(r.get("node_count")) is int
        and r["node_count"] == 2,
        "SHAPE_READ_HOLD",
    )
    check(
        type(overlap) is dict
        and set(overlap)
        == {
            "schema",
            "time_utc",
            "container_pinned",
            "graph_mutations",
            "identifiers_returned",
            "live_reconciliation_approved",
            "production_no_go",
            "property_values_returned",
            "result",
        }
        and overlap.get("schema") == "assistx.live-duplicate-read-only-owner-overlap.v1"
        and overlap.get("container_pinned") is True
        and overlap.get("graph_mutations") is False
        and overlap.get("identifiers_returned") is False
        and overlap.get("property_values_returned") is False
        and overlap.get("live_reconciliation_approved") is False
        and overlap.get("production_no_go") is True,
        "UNSAFE_OWNER_PROVENANCE",
    )
    o = overlap.get("result")
    check(
        type(o) is dict
        and set(o) == {"status", "read_only", "counts"}
        and o.get("status") == "READ_ONLY_AGGREGATE_PASS"
        and o.get("read_only") is True,
        "OWNER_READ_HOLD",
    )
    c = o.get("counts")
    check(
        type(c) is dict
        and set(c) == {"owners_a", "owners_b", "shared_owners", "serves_a", "serves_b"}
        and all(type(v) is int for v in c.values())
        and sorted((c["owners_a"], c["owners_b"])) == [1, 1]
        and c["shared_owners"] == 1
        and sorted((c["serves_a"], c["serves_b"])) == [0, 1],
        "OWNER_OR_MODEL_CENSUS_DRIFT",
    )
    check(
        type(r.get("nodes")) is list
        and len(r["nodes"]) == 2
        and type(packet["records"]) is list
        and len(packet["records"]) == 2,
        "NODE_COUNT_DRIFT",
    )
    source_records = sorted(
        (record_shape(n, source=True) for n in r["nodes"]), key=lambda s: len(s[1])
    )
    review_records = sorted(
        (record_shape(n, source=False) for n in packet["records"]),
        key=lambda s: len(s[1]),
    )
    check(
        {n["record_alias"] for n in packet["records"]}
        == {"colliding_shape_1", "colliding_shape_2"},
        "ALIAS_REUSED",
    )
    check(
        tuple(len(item[1]) for item in source_records) == (9, 17),
        "PINNED_PROPERTY_COUNT_DRIFT",
    )
    check(
        len(set(source_records[0][1]) & set(source_records[1][1])) == 7,
        "PINNED_OVERLAP_DRIFT",
    )
    check(
        source_records == review_records,
        "PRIVATE_REVIEW_NOT_EQUAL_TO_READONLY_EVIDENCE",
    )
    return {
        "status": "HOLD_REVIEW_REQUIRED",
        "duplicate_nodes": 2,
        "property_counts": [9, 17],
        "shared_property_keys": 7,
        "relationship_count": 3,
        "source_authenticity_outside_admin_verified": False,
        "reviewer_approval_verified": False,
        "production_release_authorized": False,
        "write_actions_executed": 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Offline private review, never migration"
    )
    parser.add_argument("--private-review")
    parser.add_argument("--private-shape")
    parser.add_argument("--private-owners")
    args = parser.parse_args(argv)
    if not all((args.private_review, args.private_shape, args.private_owners)):
        print(
            json.dumps(
                {
                    "status": "HOLD_PRIVATE_EVIDENCE_REQUIRED",
                    "production_release_authorized": False,
                }
            )
        )
        return 2
    try:
        review_raw = private_read(args.private_review)
        shape_raw = private_read(args.private_shape)
        overlap_raw = private_read(args.private_owners)
        decision = assess(
            decode(review_raw),
            decode(shape_raw),
            decode(overlap_raw),
            shape_raw,
            overlap_raw,
        )
    except EvidenceDrift as exc:
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
