#!/usr/bin/env python3
"""Aggregate-only read witness for ServiceEndpoint.endpoint_id identity collisions.

This diagnostic never returns endpoint_id values, node properties, element
identifiers or relationship targets. It makes no attempt to merge, delete,
rename or repair graph entities. A clean count is NOT release authorization.
"""
from __future__ import annotations

import json
import os
import sys

COLLISION_QUERY = """
MATCH (e:ServiceEndpoint)
WHERE e.endpoint_id IS NOT NULL
WITH e.endpoint_id AS identity, collect(e) AS endpoints
WHERE size(endpoints) > 1
RETURN count(*) AS collision_groups,
       coalesce(sum(size(endpoints)), 0) AS involved_nodes,
       coalesce(sum(size(endpoints) - 1), 0) AS excess_nodes,
       coalesce(max(size(endpoints)), 0) AS largest_group,
       coalesce(sum(CASE WHEN all(
           node IN endpoints
           WHERE properties(node) = properties(endpoints[0])
       ) THEN 0 ELSE 1 END), 0) AS divergent_property_groups,
       coalesce(sum(CASE WHEN all(
           node IN endpoints
           WHERE labels(node) = labels(endpoints[0])
       ) THEN 0 ELSE 1 END), 0) AS divergent_label_groups
"""

COLLISION_FIELDS = (
    "collision_groups", "involved_nodes", "excess_nodes",
    "largest_group", "divergent_property_groups", "divergent_label_groups",
)


def validate_counts(row: dict) -> dict:
    """Reject incomplete, malformed or internally inconsistent metadata."""
    if not isinstance(row, dict) or set(row) != set(COLLISION_FIELDS):
        raise ValueError("unexpected aggregate witness shape")
    if any(type(row[field]) is not int or row[field] < 0 for field in COLLISION_FIELDS):
        raise ValueError("invalid aggregate witness count")
    groups = row["collision_groups"]
    nodes = row["involved_nodes"]
    excess = row["excess_nodes"]
    largest = row["largest_group"]
    if groups == 0:
        if any(row.values()):
            raise ValueError("nonzero detail for zero collision groups")
    elif (
        largest < 2
        or nodes < 2 * groups
        or excess != nodes - groups
        or largest > nodes - 2 * (groups - 1)
        or row["divergent_property_groups"] > groups
        or row["divergent_label_groups"] > groups
    ):
        raise ValueError("contradictory aggregate witness")
    return dict(row)


def read_live_aggregate() -> dict:
    # Delayed import permits zero-dependency synthetic contract tests.
    from neo4j import GraphDatabase, READ_ACCESS

    required = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE")
    if any(not os.environ.get(key) for key in required):
        raise ValueError("explicit Neo4j database and credentials required")
    with GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        connection_timeout=5,
    ) as driver:
        with driver.session(
            database=os.environ["NEO4J_DATABASE"],
            default_access_mode=READ_ACCESS,
        ) as session:
            records = [dict(record) for record in session.run(
                COLLISION_QUERY, timeout=8,
            )]
    if len(records) != 1:
        raise ValueError("incomplete or repeated aggregate result")
    return validate_counts(records[0])


def report_from_aggregate(row: dict) -> dict:
    counts = validate_counts(row)
    return {
        "schema": "assistx.service-endpoint-identity-collision.v1",
        "status": (
            "PASS_NO_SERVICE_ENDPOINT_COLLISIONS_ONLY"
            if counts["collision_groups"] == 0
            else "HOLD_COLLIDING_SERVICE_ENDPOINT_IDENTITIES"
        ),
        "counts": counts,
        "graph_reads_only": True,
        "identifier_values_logged": False,
        "node_properties_or_records_logged": False,
        "graph_mutations_executed": False,
        "automatic_collision_repair_authorized": False,
        "production_release_authorized": False,
    }


def main() -> int:
    try:
        report = report_from_aggregate(read_live_aggregate())
    except Exception as exc:
        # Never dump raw exception text; it may contain Neo4j URIs or data.
        report = {
            "schema": "assistx.service-endpoint-identity-collision.v1",
            "status": "HOLD_COLLISION_WITNESS_UNVERIFIABLE",
            "error_type": type(exc).__name__,
            "graph_reads_only": True,
            "identifier_values_logged": False,
            "node_properties_or_records_logged": False,
            "graph_mutations_executed": False,
            "automatic_collision_repair_authorized": False,
            "production_release_authorized": False,
        }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS_NO_SERVICE_ENDPOINT_COLLISIONS_ONLY" else 2


if __name__ == "__main__":
    sys.exit(main())
