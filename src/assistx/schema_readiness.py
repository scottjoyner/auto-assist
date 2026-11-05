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
SHOW_CURRENT_USER = "SHOW CURRENT USER YIELD roles RETURN roles"


class SchemaReadinessHold(RuntimeError):
    """No sensitive data: runtime must not attempt schema repair."""


def inspect_runtime_principal(session: Any, expected_role: str) -> dict[str, bool]:
    """Require one explicitly pinned, non-admin custom runtime role.

    This cannot prove exact native GRANT/DENY scope. An independent privilege
    and cross-database negative test is still mandatory before release.
    """
    if (
        not isinstance(expected_role, str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{2,48}", expected_role)
        or expected_role in {"admin", "editor", "publisher", "reader",
                             "architect", "public"}
    ):
        raise SchemaReadinessHold("operator-pinned restricted runtime role missing")
    try:
        rows = list(session.run(SHOW_CURRENT_USER))
        if len(rows) != 1:
            raise SchemaReadinessHold("runtime principal introspection is ambiguous")
        roles = rows[0]["roles"]
        if not isinstance(roles, (list, tuple)) or not roles:
            raise SchemaReadinessHold("runtime principal role evidence missing")
        normalized = [role.lower() for role in roles if isinstance(role, str)]
        if len(normalized) != len(roles) or len(set(normalized)) != len(normalized):
            raise SchemaReadinessHold("runtime principal role evidence malformed")
        actual = set(normalized)
        if expected_role not in actual or not actual.issubset({expected_role, "public"}):
            raise SchemaReadinessHold("runtime principal has unapproved role")
    except SchemaReadinessHold:
        raise
    except Exception:
        raise SchemaReadinessHold("read-only runtime identity unavailable") from None
    return {"pinned_runtime_role_observed": True,
            "native_privilege_isolation_proven": False,
            "production_release_authorized": False}


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
