#!/usr/bin/env python3
"""Read-only AssistX schema drift witness.

Compares literals declared by Neo4jClient.ensure_schema() with Neo4j's
SHOW CONSTRAINTS / SHOW INDEXES metadata. Never runs DDL, graph-data queries,
or credential changes. PASS is a metadata witness, not release authorization.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import sys

CONSTRAINT = re.compile(
    r"CREATE CONSTRAINT IF NOT EXISTS FOR \(\w+:(\w+)\) "
    r"REQUIRE \w+\.(\w+) IS UNIQUE",
    re.IGNORECASE,
)
INDEX = re.compile(
    r"CREATE INDEX IF NOT EXISTS FOR \(\w+:(\w+)\) ON \(\w+\.(\w+)\)",
    re.IGNORECASE,
)
SOURCE_DEFAULT = Path("/app/src/assistx/neo4j_client.py")
CONSTRAINT_QUERY = (
    "SHOW CONSTRAINTS YIELD type, entityType, labelsOrTypes, properties "
    "RETURN type, entityType, labelsOrTypes, properties"
)
INDEX_QUERY = (
    "SHOW INDEXES YIELD type, entityType, labelsOrTypes, properties, "
    "state, owningConstraint "
    "RETURN type, entityType, labelsOrTypes, properties, "
    "state, owningConstraint"
)


class IncompleteSchemaDeclaration(ValueError):
    """Source drift prevents trustworthy enumeration of schema obligations."""


def declared_schema(source: str) -> tuple[set[tuple[str, str]], set[tuple[str, str]], int]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise IncompleteSchemaDeclaration("schema source cannot be parsed") from exc
    classes = [
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "Neo4jClient"
    ]
    if len(classes) != 1:
        raise IncompleteSchemaDeclaration("Neo4jClient class is not unique")
    methods = [
        n for n in classes[0].body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "ensure_schema"
    ]
    if len(methods) != 1:
        raise IncompleteSchemaDeclaration("ensure_schema method is not unique")
    assignments = [
        node for node in methods[0].body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "cypher" for t in node.targets)
    ]
    if len(assignments) != 1:
        raise IncompleteSchemaDeclaration("cypher declaration list is not statically known")
    try:
        statements = ast.literal_eval(assignments[0].value)
    except (SyntaxError, ValueError, TypeError, MemoryError) as exc:
        raise IncompleteSchemaDeclaration("cypher declaration is not literal") from exc
    if not isinstance(statements, list) or not statements:
        raise IncompleteSchemaDeclaration("missing schema declaration list")
    constraints: set[tuple[str, str]] = set()
    indexes: set[tuple[str, str]] = set()
    for statement in statements:
        if not isinstance(statement, str):
            raise IncompleteSchemaDeclaration("non-string schema declaration")
        normalized = " ".join(statement.split())
        cm = CONSTRAINT.fullmatch(normalized)
        im = INDEX.fullmatch(normalized)
        if cm:
            pair = cm.group(1), cm.group(2)
            if pair in constraints:
                raise IncompleteSchemaDeclaration("duplicate constraint obligation")
            constraints.add(pair)
        elif im:
            pair = im.group(1), im.group(2)
            if pair in indexes:
                raise IncompleteSchemaDeclaration("duplicate index obligation")
            indexes.add(pair)
        else:
            raise IncompleteSchemaDeclaration("unrecognized schema declaration")
    return constraints, indexes, len(statements)


def _node_property_pair(row: dict) -> tuple[str, str] | None:
    labels = row.get("labelsOrTypes")
    props = row.get("properties")
    if (
        row.get("entityType") != "NODE"
        or not isinstance(labels, (list, tuple))
        or not isinstance(props, (list, tuple))
        or len(labels) != 1 or len(props) != 1
        or not isinstance(labels[0], str) or not isinstance(props[0], str)
    ):
        return None
    return labels[0], props[0]


def assess_metadata(
    expected_constraints: set[tuple[str, str]],
    expected_indexes: set[tuple[str, str]],
    constraints: list[dict],
    indexes: list[dict],
    *,
    include_missing_names: bool = False,
) -> dict:
    real_constraints = {
        pair for row in constraints
        if (pair := _node_property_pair(row)) is not None
        and "UNIQUE" in str(row.get("type", "")).upper()
    }
    range_indexes = {
        pair: str(row.get("state", "")).upper()
        for row in indexes
        if (pair := _node_property_pair(row)) is not None
        and str(row.get("type", "")).upper() in ("RANGE", "BTREE")
        and row.get("owningConstraint") is None
    }
    online_indexes = {
        pair for pair, state in range_indexes.items() if state == "ONLINE"
    }
    missing_constraints = expected_constraints - real_constraints
    missing_indexes = expected_indexes - online_indexes
    offline_indexes = {
        pair for pair in (expected_indexes & set(range_indexes))
        if range_indexes[pair] != "ONLINE"
    }
    complete = not (missing_constraints or missing_indexes)
    result = {
        "status": (
            "PASS_DECLARED_SCHEMA_METADATA_PRESENT"
            if complete else "HOLD_DECLARED_SCHEMA_MISSING_OR_OFFLINE"
        ),
        "expected_unique_constraints": len(expected_constraints),
        "matched_unique_constraints": len(expected_constraints & real_constraints),
        "missing_unique_constraints_count": len(missing_constraints),
        "expected_standalone_range_indexes": len(expected_indexes),
        "matched_online_indexes": len(expected_indexes & online_indexes),
        "missing_or_offline_indexes_count": len(missing_indexes),
        "offline_declared_indexes_count": len(offline_indexes),
        "other_schema_objects_may_exist": True,
        "all_obligations_satisfied": complete,
    }
    if include_missing_names:
        result["missing_constraints"] = [
            {"node_label": label, "property": prop}
            for label, prop in sorted(missing_constraints)
        ]
        result["missing_or_offline_indexes"] = [
            {"node_label": label, "property": prop}
            for label, prop in sorted(missing_indexes)
        ]
    return result


def read_live_schema() -> tuple[list[dict], list[dict]]:
    # Lazy import: AST parser and synthetic tests work without Neo4j installed.
    from neo4j import GraphDatabase, READ_ACCESS

    required = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE")
    if any(not os.environ.get(key) for key in required):
        raise ValueError("an explicit database and credentials are required")
    with GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        connection_timeout=5,
    ) as driver:
        with driver.session(
            database=os.environ["NEO4J_DATABASE"],
            default_access_mode=READ_ACCESS,
        ) as session:
            constraints = [
                dict(row) for row in session.run(CONSTRAINT_QUERY, timeout=8)
            ]
            indexes = [dict(row) for row in session.run(INDEX_QUERY, timeout=8)]
    return constraints, indexes


def audit(
    source: str,
    constraints: list[dict],
    indexes: list[dict],
    *,
    include_missing_names: bool = False,
) -> dict:
    declared_constraints, declared_indexes, count = declared_schema(source)
    result = assess_metadata(
        declared_constraints, declared_indexes, constraints, indexes,
        include_missing_names=include_missing_names,
    )
    result["parsed_declarations"] = count
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument(
        "--include-missing-names", action="store_true",
        help="Print schema names only for an authorized private owner review",
    )
    args = parser.parse_args(argv)
    report: dict = {
        "schema": "assistx.neo4j-schema-drift-readonly.v1",
        "status": "HOLD_UNVERIFIED",
        "schema_metadata_queries_only": True,
        "graph_records_queried": False,
        "graph_ddl_or_write_executed": False,
        "credential_values_or_hashes_logged": False,
        "production_release_authorized": False,
    }
    try:
        source = args.source.read_text()
        # Parse all obligations before connecting to the target database.
        declared_schema(source)
        constraints, indexes = read_live_schema()
        result = audit(
            source, constraints, indexes,
            include_missing_names=args.include_missing_names,
        )
        report.update(result)
    except (Exception,) as exc:
        # No raw exception details: driver errors can contain connection data.
        report["status"] = "HOLD_UNVERIFIABLE_SOURCE_OR_METADATA"
        report["error_type"] = type(exc).__name__
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS_DECLARED_SCHEMA_METADATA_PRESENT" else 2


if __name__ == "__main__":
    sys.exit(main())
