#!/usr/bin/env python3
"""Actual disposable Neo4j uniqueness/relationship acceptance for model IDs.

RESEARCH ONLY: requires CI_DISPOSABLE_ENDPOINT_NAMESPACE=synthetic-only;
fixed localhost synthetic driver/password, no fleet URI, DB mounts or secrets.
"""

from __future__ import annotations

import ast
import hashlib
import os
import time
from pathlib import Path

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

ROOT = Path(__file__).resolve().parents[1]
URI = "bolt://127.0.0.1:27889"
SYNTHETIC_PASSWORD = "synthetic-graph-service-key-identity-only-2026"
SERVICE_CONSTRAINT = (
    "CREATE CONSTRAINT service_identity_synthetic IF NOT EXISTS FOR (e:ServiceEndpoint) REQUIRE e.endpoint_id IS UNIQUE"
)
MODEL_CONSTRAINT = (
    "CREATE CONSTRAINT model_identity_synthetic IF NOT EXISTS "
    "FOR (e:ModelEndpoint) REQUIRE e.model_endpoint_id IS UNIQUE"
)
RAW_ID = "test-disposable-shared-endpoint"
MODEL_KEY = "model_ep:v1:" + hashlib.sha256(RAW_ID.encode("utf-8")).hexdigest()


def _source_query(function_name: str, text: str) -> str:
    # Use the *actual* source function's Cypher, not a duplicate test query.
    tree = ast.parse((ROOT / "src" / "assistx" / "swarm_core.py").read_text())
    target = next(
        x for x in tree.body if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) and x.name == function_name
    )
    found = [
        x.args[0].value
        for x in ast.walk(target)
        if isinstance(x, ast.Call)
        and isinstance(x.func, ast.Attribute)
        and x.func.attr == "run"
        and x.args
        and isinstance(x.args[0], ast.Constant)
        and isinstance(x.args[0].value, str)
        and text in x.args[0].value
    ]
    if len(found) != 1:
        raise AssertionError("ambiguous_or_missing_exact_source_query")
    return found[0]


def _ready(driver) -> None:
    for _ in range(90):
        try:
            with driver.session(database="neo4j") as s:
                if s.run("RETURN 1 AS ready").single()["ready"] == 1:
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError("disposable_graph_not_ready")


def _one_bool(session, query: str) -> bool:
    result = session.run(query).single()
    return bool(result and result["valid"] is True)


def main() -> None:
    if os.environ.get("CI_DISPOSABLE_ENDPOINT_NAMESPACE") != "synthetic-only":
        raise RuntimeError("isolated_synthetic_environment_not_proven")
    ordinary_query = _source_query("upsert_swarm_node", "MERGE (e:ServiceEndpoint {endpoint_id:$endpoint_id})")
    model_query = _source_query(
        "upsert_model_endpoint", "MERGE (e:ModelEndpoint:ServiceEndpoint {model_endpoint_id:$endpoint_id})"
    )
    if not all(
        x in model_query
        for x in (
            "e.endpoint_id=$service_graph_id",
            "e.endpoint_id=coalesce(e.endpoint_id, $service_graph_id)",
        )
    ):
        raise AssertionError("model_key_must_be_created_not_implicit_legacy_rewritten")
    if RAW_ID in MODEL_KEY or not MODEL_KEY.startswith("model_ep:v1:"):
        raise AssertionError("scoped_key_not_pseudonymous")
    driver = GraphDatabase.driver(
        URI,
        auth=("neo4j", SYNTHETIC_PASSWORD),
        connection_timeout=5,
        max_connection_pool_size=3,
    )
    try:
        _ready(driver)
        with driver.session(database="neo4j") as s:
            s.run(SERVICE_CONSTRAINT).consume()
            s.run(MODEL_CONSTRAINT).consume()
            s.run("CREATE (n:SwarmNode {node_id:'disposable-graph-owner'})").consume()
            s.run(
                ordinary_query,
                {
                    "node_id": "disposable-graph-owner",
                    "endpoint_id": RAW_ID,
                    "props": {"service_type": "api", "status": "online"},
                },
            ).consume()
            props = {"model_endpoint_id": RAW_ID, "status": "online", "node_id": "disposable-graph-owner"}
            args = {
                "node_id": "disposable-graph-owner",
                "endpoint_id": RAW_ID,
                "service_graph_id": MODEL_KEY,
                "props": props,
            }
            s.run(model_query, args).consume()
            # Exact same event must not create a second node or change the key.
            s.run(model_query, args).consume()
            s.run(
                "MATCH (e:ModelEndpoint {model_endpoint_id:$mid}) "
                "MERGE (m:Model {model_id:'disposable-served-model'}) "
                "MERGE (e)-[:SERVES]->(m)",
                {"mid": RAW_ID},
            ).consume()
            # Preserve the owner and model relationship (no mutation of live DB).
            if not _one_bool(
                s,
                "MATCH (n:SwarmNode {node_id:'disposable-graph-owner'})-[:EXPOSES]->(e:ServiceEndpoint) "
                "WITH collect(e) AS ep "
                "RETURN size(ep)=2 AND size([x IN ep WHERE x:ModelEndpoint])=1 "
                "AND size([x IN ep WHERE x.endpoint_id STARTS WITH 'model_ep:v1:'])=1 AS valid",
            ):
                raise AssertionError("synthetic_two_endpoint_labels_or_owner_relationships_missing")
            if not _one_bool(
                s,
                "MATCH (e:ModelEndpoint)-[:SERVES]->(m:Model {model_id:'disposable-served-model'}) "
                "RETURN e.model_endpoint_id IS NOT NULL AND e.endpoint_id STARTS WITH 'model_ep:v1:' AS valid",
            ):
                raise AssertionError("synthetic_model_service_relation_missing")
            # Simulate ONLY in disposable graph: old raw ID reintroduced after
            # removing the synthetic constraint, must block uniqueness DDL.
            s.run("DROP CONSTRAINT service_identity_synthetic IF EXISTS").consume()
            s.run(
                "MATCH (e:ModelEndpoint {model_endpoint_id:$mid}) SET e.endpoint_id=$raw",
                {"mid": RAW_ID, "raw": RAW_ID},
            ).consume()
            try:
                s.run(SERVICE_CONSTRAINT).consume()
            except Neo4jError as exc:
                code = getattr(exc, "code", "")
                if not code.startswith("Neo.ClientError.Schema."):
                    raise AssertionError("negative_schema_failure_was_not_native_constraint_denial") from None
            else:
                raise AssertionError("historical_duplicate_was_not_denied")
            # Explicit operator-style synthetic repair, never called by normal
            # upsert_model_endpoint. Prove uniqueness and relationships survive.
            s.run(
                "MATCH (e:ModelEndpoint {model_endpoint_id:$mid}) SET e.endpoint_id=$scoped",
                {"mid": RAW_ID, "scoped": MODEL_KEY},
            ).consume()
            s.run(SERVICE_CONSTRAINT).consume()
            if not _one_bool(
                s,
                "MATCH (e:ModelEndpoint)-[:SERVES]->(m:Model {model_id:'disposable-served-model'}) "
                "MATCH (n:SwarmNode {node_id:'disposable-graph-owner'})-[:EXPOSES]->(e) "
                "RETURN e.endpoint_id STARTS WITH 'model_ep:v1:' AS valid",
            ):
                raise AssertionError("migration_lost_model_relationship")
            matches = s.run(
                "SHOW CONSTRAINTS YIELD type,labelsOrTypes,properties WHERE "
                "labelsOrTypes=['ServiceEndpoint'] AND properties=['endpoint_id'] RETURN type"
            ).data()
            if len(matches) != 1 or matches[0]["type"] != "UNIQUENESS":
                raise AssertionError("synthetic_service_constraint_not_reinstated")
        print("PASS: synthetic model/service coexist, collision denied, explicit repair preserves edges")
        print("HOLD: production migration not authorized, no fleet or credential access")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
