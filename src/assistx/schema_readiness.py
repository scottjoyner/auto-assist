"""Read-only, fail-closed Neo4j runtime schema gate for external migration.

Never runs DDL, GRAPH writes, procedure calls, or privileged repair.
Schema creation stays in an explicitly separated, owner-controlled bootstrap.
All reported errors deliberately omit private database names and credentials.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

CONSTRAINT = re.compile(
    r"^CREATE CONSTRAINT IF NOT EXISTS FOR \(\w+:([A-Za-z]\w*)\)"
    r"\s+REQUIRE\s+\w+\.([A-Za-z]\w*)\s+IS UNIQUE$"
)
INDEX = re.compile(
    r"^CREATE INDEX IF NOT EXISTS FOR \(\w+:([A-Za-z]\w*)\)"
    r"\s+ON\s+\(\w+\.([A-Za-z]\w*)\)$"
)

# These calls are schema metadata reads (and therefore must be granted only
# on the intended database). No CREATE or CALL is part of runtime preflight.
SHOW_CONSTRAINTS = (
    "SHOW CONSTRAINTS YIELD type, entityType, labelsOrTypes, properties "
    "RETURN type, entityType, labelsOrTypes, properties"
)
SHOW_INDEXES = (
    "SHOW INDEXES YIELD type, entityType, labelsOrTypes, properties, state "
    "RETURN type, entityType, labelsOrTypes, properties, state"
)


class SchemaReadinessHold(RuntimeError):
    """No sensitive data: runtime must not attempt schema repair."""


def expected_schema(operations: Iterable[str]) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Reject unexpected DDL syntax or duplicate declared obligations."""
    constraints: set[tuple[str, str]] = set()
    indexes: set[tuple[str, str]] = set()
    total = 0
    for operation in operations:
        total += 1
        if not isinstance(operation, str):
            raise SchemaReadinessHold("schema catalog has invalid operation")
        op = operation.strip()
        con = CONSTRAINT.fullmatch(op)
        ix = INDEX.fullmatch(op)
        if bool(con) == bool(ix):
            raise SchemaReadinessHold("schema catalog syntax not independently recognized")
        target = constraints if con else indexes
        m = con if con else ix
        key = (m.group(1), m.group(2))
        if key in target:
            raise SchemaReadinessHold("duplicate schema obligation")
        target.add(key)
    # Pinned to 2026-11-05 source catalog: fail if the runtime catalog silently
    # loses an obligation during future refactors.
    if total != 139 or len(constraints) != 53 or len(indexes) != 86:
        raise SchemaReadinessHold("schema catalog count changed without review")
    return constraints, indexes


def _row_tuple(row: Any) -> tuple[str, str] | None:
    """Accept only one explicit node-label/field pair, never infer an alias."""
    try:
        labels = row["labelsOrTypes"]
        fields = row["properties"]
        if (
            row["entityType"] != "NODE"
            or not isinstance(labels, (tuple, list))
            or not isinstance(fields, (tuple, list))
            or len(labels) != 1
            or len(fields) != 1
            or not isinstance(labels[0], str)
            or not isinstance(fields[0], str)
        ):
            return None
        return labels[0], fields[0]
    except (KeyError, ValueError, TypeError):
        return None


def inspect_schema_readiness(session: Any, operations: Iterable[str]) -> dict[str, int | bool]:
    """Run only two SHOW statements; return aggregate counts, never graph data.

    A metadata permission failure, missing constraint, offline index or unknown
    schema catalog is a HOLD. A clean result is *only* source/schema-readiness,
    NOT privilege isolation, backup, COMMIT authority or release approval.
    """
    expected_constraints, expected_indexes = expected_schema(operations)
    try:
        actual_constraints: set[tuple[str, str]] = set()
        constraint_results = list(session.run(SHOW_CONSTRAINTS))
        for row in constraint_results:
            if row["type"] == "UNIQUENESS":
                entry = _row_tuple(row)
                if entry is not None:
                    actual_constraints.add(entry)

        actual_indexes: set[tuple[str, str]] = set()
        index_results = list(session.run(SHOW_INDEXES))
        for row in index_results:
            if row["type"] == "RANGE" and row["state"] == "ONLINE":
                entry = _row_tuple(row)
                if entry is not None:
                    actual_indexes.add(entry)
    except Exception:
        # Do not include the driver exception: it can contain a connection
        # address, Neo4j user, query or other private transport details.
        raise SchemaReadinessHold("read-only schema metadata unavailable") from None

    absent_constraints = expected_constraints - actual_constraints
    absent_indexes = expected_indexes - actual_indexes
    if absent_constraints or absent_indexes:
        # Aggregate-only report. Do not name one missing privileged resource
        # in logs or return identifiers to unauthenticated clients.
        raise SchemaReadinessHold(
            "required schema not independently ready "
            f"(missing_constraints={len(absent_constraints)}, "
            f"missing_online_indexes={len(absent_indexes)})"
        )
    return {
        "verified_constraints": len(expected_constraints),
        "verified_online_indexes": len(expected_indexes),
        "read_only": True,
        "production_release_authorized": False,
    }
