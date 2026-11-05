#!/usr/bin/env python3
"""Disposable Neo4j Enterprise 5.26 schema/runtime-principal acceptance.

RESEARCH ONLY. Explicitly requires CI_DISPOSABLE_NEO4J=synthetic-only.
Never targets fleet credentials, NAS, production mounts, or a remote host.
All DDL/negative writes target a fresh throwaway localhost Docker database.
"""
from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import time

from neo4j import GraphDatabase, READ_ACCESS
from neo4j.exceptions import Neo4jError

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_URI = "bolt://127.0.0.1:27888"
ADMIN_PASSWORD = "synthetic-neo4j-enterprise-schema-only-2026"
RUNTIME_PASSWORD = "synthetic-neo4j-restricted-only-2026"

spec = importlib.util.spec_from_file_location(
    "isolated_schema_readiness", ROOT / "src" / "assistx" / "schema_readiness.py"
)
readiness = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(readiness)


def _catalog() -> list[str]:
    src = ast.parse((ROOT / "src" / "assistx" / "neo4j_client.py").read_text())
    client = next(n for n in src.body if isinstance(n, ast.ClassDef)
                  and n.name == "Neo4jClient")
    method = next(n for n in client.body if isinstance(n, ast.FunctionDef)
                  and n.name == "schema_operations")
    assignment = next(n for n in method.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "cypher"
                              for t in n.targets))
    return ast.literal_eval(assignment.value)


def _wait_for_server(driver) -> None:
    for _ in range(90):
        try:
            with driver.session(database="neo4j") as s:
                if s.run("RETURN 1 AS result").single()["result"] == 1:
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError("disposable Neo4j did not become ready")


def _expect_denied(session, query: str) -> None:
    try:
        session.run(query).consume()
    except Neo4jError as exc:
        # Wrong syntax, connection failure or a missing database is NOT proof
        # of privilege isolation. Require a native security refusal.
        if getattr(exc, "code", None) == "Neo.ClientError.Security.Forbidden":
            return
        raise AssertionError(
            "negative action failed for reason other than native authorization"
        ) from None
    raise AssertionError("unapproved database or DBMS mutation was accepted")


def main() -> None:
    if os.getenv("CI_DISPOSABLE_NEO4J") != "synthetic-only":
        raise RuntimeError("disposable-only runtime evidence is not authorized")
    # Never accept caller-provided URI, credentials, database name or fixture
    # source. The GitHub workflow publishes this synthetic container only
    # on localhost of an ephemeral hosted runner.
    catalog = _catalog()
    if len(catalog) != 139:
        raise RuntimeError("unreviewed schema catalog")
    admin = GraphDatabase.driver(
        EXPECTED_URI, auth=("neo4j", ADMIN_PASSWORD),
        connection_timeout=6, max_connection_pool_size=4,
    )
    runtime = None
    try:
        _wait_for_server(admin)
        with admin.session(database="neo4j") as session:
            try:
                readiness.inspect_schema_readiness(session, catalog)
            except readiness.SchemaReadinessHold:
                pass
            else:
                raise AssertionError("fresh synthetic graph unexpectedly had full app schema")
            try:
                readiness.inspect_runtime_principal(session, "assistx_runtime")
            except readiness.SchemaReadinessHold:
                pass
            else:
                raise AssertionError("shared native administrator unexpectedly accepted")

            # Explicit privileged bootstrap under isolated, throwaway admin
            # only. No graph data, root/EFI, external registry, user secrets,
            # file mounts or production database are ever involved.
            for query in catalog:
                session.run(query).consume()

        with admin.session(database="system") as session:
            session.run("CREATE ROLE assistx_runtime IF NOT EXISTS").consume()
            session.run(
                "CREATE USER assistx_ci_runtime SET PASSWORD $password CHANGE NOT REQUIRED",
                password=RUNTIME_PASSWORD,
            ).consume()
            session.run(
                "GRANT ROLE assistx_runtime TO assistx_ci_runtime"
            ).consume()
            session.run(
                "GRANT ACCESS ON DATABASE neo4j TO assistx_runtime"
            ).consume()
            session.run(
                "GRANT SHOW CONSTRAINT ON DATABASE neo4j TO assistx_runtime"
            ).consume()
            session.run(
                "GRANT SHOW INDEX ON DATABASE neo4j TO assistx_runtime"
            ).consume()

        runtime = GraphDatabase.driver(
            EXPECTED_URI, auth=("assistx_ci_runtime", RUNTIME_PASSWORD),
            connection_timeout=6, max_connection_pool_size=4,
        )
        for _ in range(60):
            try:
                with runtime.session(
                    database="neo4j", default_access_mode=READ_ACCESS
                ) as session:
                    roles = readiness.inspect_runtime_principal(
                        session, "assistx_runtime"
                    )
                    report = readiness.inspect_schema_readiness(session, catalog)
                break
            except readiness.SchemaReadinessHold:
                time.sleep(1)
        else:
            raise AssertionError("restricted real Neo4j schema readiness failed")

        if not roles["pinned_runtime_role_observed"]:
            raise AssertionError("restricted identity was not verified")
        if report["verified_constraints"] != 53 or report["verified_online_indexes"] != 86:
            raise AssertionError("real Neo4j schema count not accepted")

        # Negative controls prove the restricted principal does not acquire
        # schema administration merely because it can SHOW schema metadata.
        with runtime.session(database="neo4j") as session:
            _expect_denied(
                session,
                "CREATE INDEX synthetic_forbidden_runtime_index "
                "IF NOT EXISTS FOR (n:Synthetic) ON (n.field)",
            )
        with runtime.session(database="system") as session:
            _expect_denied(session, "CREATE ROLE synthetic_illicit_runtime_role")

        print(
            "PASS isolated Neo4j Enterprise schema: "
            "53 uniqueness constraints, 86 online indexes, "
            "explicit non-admin runtime role, schema/DBMS denial; "
            "production_release_authorized=false"
        )
    finally:
        if runtime is not None:
            runtime.close()
        admin.close()


if __name__ == "__main__":
    main()
