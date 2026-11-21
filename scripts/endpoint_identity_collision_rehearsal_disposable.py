#!/usr/bin/env python3
"""Synthetic legacy-collision rehearsal; never a live graph migration.

Imports exact parent draft #324 disposable harness. It refuses to run absent
explicit --run-disposable and inherits hard no-ports/no-mounts/internal-network
isolation, synthetic credentials, source-only writer, and guaranteed cleanup.

This intentionally uses test-only schema DROP/CREATE and a test-only DELETE
to validate preservation of owner/model semantics. Never deploy or import the
reconciliation function into production; it is NOT an approved data migration.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "scripts" / "endpoint_identity_candidate_disposable.py"
spec = importlib.util.spec_from_file_location("pr324_disposable_harness", PARENT)
if spec is None or spec.loader is None:
    raise RuntimeError("pinned disposable harness unavailable")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

SCHEMA = "assistx.synthetic-legacy-collision-rehearsal.v1"


class SyntheticCollisionHold(RuntimeError):
    """Unreviewed or semantically ambiguous duplicate cannot be reconciled."""


def assess_exact_synthetic_pair(rows: list[dict], *, expected_owner: str, expected_endpoint: str) -> dict:
    """In-memory structural intake. No approval and no graph mutation.

    This is purposely NOT a general graph reconciliation algorithm; it only
    accepts an EXACTLY understood, newly generated test fixture.
    """
    if (
        not isinstance(rows, list)
        or len(rows) != 2
        or not isinstance(expected_owner, str)
        or not isinstance(expected_endpoint, str)
    ):
        raise SyntheticCollisionHold("ambiguous synthetic pair or missing scope")
    entries = []
    for item in rows:
        if type(item) is not dict or set(item) != {
            "labels",
            "owners",
            "kind",
            "mid",
            "port",
            "relationship_types",
            "serves",
            "endpoint",
            "property_keys",
        }:
            raise SyntheticCollisionHold("unrecognized synthetic data schema")
        if (
            item["endpoint"] != expected_endpoint
            or item["owners"] != [expected_owner]
            or item["kind"] != "model_endpoint"
            or type(item["labels"]) is not list
            or type(item["relationship_types"]) is not list
            or type(item["serves"]) is not int
            or type(item["property_keys"]) is not list
        ):
            raise SyntheticCollisionHold("owner, type or edge ambiguity")
        if not all(isinstance(label, str) for label in item["labels"]) or not all(
            isinstance(edge, str) for edge in item["relationship_types"]
        ):
            raise SyntheticCollisionHold("malformed synthetic label or edge type")
        if not all(isinstance(key, str) for key in item["property_keys"]):
            raise SyntheticCollisionHold("malformed synthetic node property key")
        labels = set(item["labels"])
        types = set(item["relationship_types"])
        properties = set(item["property_keys"])
        if labels not in ({"ServiceEndpoint"}, {"ServiceEndpoint", "ModelEndpoint"}):
            raise SyntheticCollisionHold("unknown labels or missing service identity")
        if "ModelEndpoint" in labels:
            if (
                properties != {"endpoint_id", "model_endpoint_id", "service_type"}
                or item["mid"] != expected_endpoint
                or item["port"] is not None
                or types != {"EXPOSES", "SERVES"}
                or item["serves"] != 1
            ):
                raise SyntheticCollisionHold("model subtype has unexpected shape")
            entries.append("model")
        else:
            if (
                properties != {"endpoint_id", "service_type", "port"}
                or item["mid"] is not None
                or type(item["port"]) is not int
                or types != {"EXPOSES"}
                or item["serves"] != 0
            ):
                raise SyntheticCollisionHold("legacy service has unexpected edges")
            entries.append("service")
    if sorted(entries) != ["model", "service"]:
        raise SyntheticCollisionHold("not one service plus one model")
    return {
        "status": "SYNTHETIC_PAIR_STRUCTURALLY_REVIEWABLE_ONLY",
        "requires_external_operator_approval": True,
        "production_release_authorized": False,
    }


def _read_pair(session, endpoint):
    query = """
    MATCH (e:ServiceEndpoint {endpoint_id:$eid})
    OPTIONAL MATCH (owner:SwarmNode)-[:EXPOSES]->(e)
    WITH e, collect(DISTINCT owner.node_id) AS owners
    OPTIONAL MATCH (e)-[r]-()
    WITH e, owners, collect(DISTINCT type(r)) AS edge_types
    OPTIONAL MATCH (e)-[:SERVES]->(model:Model)
    RETURN keys(e) AS property_keys, labels(e) AS labels, owners, e.service_type AS kind,
           e.model_endpoint_id AS mid, e.port AS port,
           edge_types AS relationship_types, count(DISTINCT model) AS serves,
           e.endpoint_id AS endpoint
    """
    return [dict(row) for row in session.run(query, eid=endpoint)]


def _aggregate(session, endpoint):
    row = session.run(
        """
    MATCH (e:ServiceEndpoint {endpoint_id:$eid})
    OPTIONAL MATCH (:SwarmNode)-[x:EXPOSES]->(e)
    WITH e, count(x) AS owner_links
    OPTIONAL MATCH (e)-[s:SERVES]->(:Model)
    RETURN count(e) AS nodes, coalesce(sum(owner_links),0) AS exposes,
           count(s) AS serves
    """,
        eid=endpoint,
    ).single()
    if row is None:
        raise SyntheticCollisionHold("missing synthetic aggregate")
    return {"nodes": int(row["nodes"]), "exposes": int(row["exposes"]), "serves": int(row["serves"])}


def _manual_lab_reconcile(tx, *, endpoint, owner):
    """DO NOT REUSE: scoped disposable graph and exact lab fixture ONLY."""
    if not endpoint.startswith(harness.PREFIX + ":"):
        raise SyntheticCollisionHold("non-synthetic endpoint forbidden")
    rows = _read_pair(tx, endpoint)
    assess_exact_synthetic_pair(rows, expected_owner=owner, expected_endpoint=endpoint)
    initial = _aggregate(tx, endpoint)
    if initial != {"nodes": 2, "exposes": 2, "serves": 1}:
        raise SyntheticCollisionHold("synthetic relationship census drift")
    # The graph is disposable, the resolution is *explicitly* selected by
    # this lab, and no real data or credentials can reach this transaction.
    # Plain DELETE intentionally refuses any hidden extra relationship.
    records = list(
        tx.run(
            """
        MATCH (s:ServiceEndpoint {endpoint_id:$eid})
        WHERE NOT s:ModelEndpoint
        MATCH (m:ServiceEndpoint:ModelEndpoint {endpoint_id:$eid})
        MATCH (o:SwarmNode {node_id:$owner})-[link:EXPOSES]->(s)
        MATCH (o)-[:EXPOSES]->(m)
        SET m.port = s.port
        DELETE link
        DELETE s
        RETURN count(m) AS merged
    """,
            eid=endpoint,
            owner=owner,
        )
    )
    if len(records) != 1 or records[0]["merged"] != 1:
        raise SyntheticCollisionHold("explicit lab-only resolution incomplete")
    after = _aggregate(tx, endpoint)
    if after != {"nodes": 1, "exposes": 1, "serves": 1}:
        raise SyntheticCollisionHold("synthetic graph relationship loss")
    return {"synthetic_nodes": 1, "semantic_exposes": 1, "semantic_serves": 1, "production_release_authorized": False}


def _fault_after_synthetic_reconcile(tx, *, endpoint, owner):
    """Inject a deterministic exception AFTER a test-only graph mutation.

    The caller MUST execute this callback through a managed transaction.
    Neo4j must roll back every change when this exception escapes.
    """
    _manual_lab_reconcile(tx, endpoint=endpoint, owner=owner)
    raise SyntheticCollisionHold("intentional synthetic post-write rollback")


def extra_scenarios(driver, results):
    eid = harness.PREFIX + ":legacy-two-node"
    owner = harness.PREFIX + ":owner"
    modelid = harness.PREFIX + ":model"
    with driver.session(database="neo4j") as session:
        # Parent writer tests have completed, with no concurrent workers left.
        # Deliberate test-only DROP is scoped to the isolated disposable DB.
        session.run("DROP CONSTRAINT canonical_service_endpoint IF EXISTS").consume()
        session.run(
            """
            CREATE (o:SwarmNode {node_id:$owner}),
                   (s:ServiceEndpoint {endpoint_id:$eid,service_type:'model_endpoint',port:4321}),
                   (m:ServiceEndpoint:ModelEndpoint {endpoint_id:$eid,
                        model_endpoint_id:$eid,service_type:'model_endpoint'}),
                   (model:Model {model_id:$modelid}),
                   (o)-[:EXPOSES]->(s),(o)-[:EXPOSES]->(m),
                   (m)-[:SERVES]->(model)
        """,
            owner=owner,
            eid=eid,
            modelid=modelid,
        ).consume()
        original = _aggregate(session, eid)
        results["legacy_synthetic_two_nodes_and_edges_reproduced"] = original == {"nodes": 2, "exposes": 2, "serves": 1}
        reviewed = assess_exact_synthetic_pair(_read_pair(session, eid), expected_owner=owner, expected_endpoint=eid)
        results["legacy_pair_needs_explicit_resolution"] = (
            reviewed["requires_external_operator_approval"] is True
            and reviewed["production_release_authorized"] is False
        )
        try:
            session.run("""
                CREATE CONSTRAINT canonical_service_endpoint IF NOT EXISTS
                FOR (e:ServiceEndpoint) REQUIRE e.endpoint_id IS UNIQUE
            """).consume()
            results["duplicate_blocks_uniqueness_ddl"] = False
        except Exception:
            results["duplicate_blocks_uniqueness_ddl"] = _aggregate(session, eid) == original
        # Physical synthetic negative: a stored node can carry an extra
        # key that the original projection silently omitted. A safe preview
        # MUST inspect the complete keys(e) inventory and HOLD on surprises.
        session.run(
            """
            MATCH (s:ServiceEndpoint {endpoint_id:$eid})
            WHERE NOT s:ModelEndpoint
            SET s.unreviewed_provenance = 'synthetic-only'
        """,
            eid=eid,
        ).consume()
        try:
            assess_exact_synthetic_pair(_read_pair(session, eid), expected_owner=owner, expected_endpoint=eid)
            results["hidden_stored_property_requires_hold"] = False
        except SyntheticCollisionHold:
            results["hidden_stored_property_requires_hold"] = _aggregate(session, eid) == original
        finally:
            session.run(
                """
                MATCH (s:ServiceEndpoint {endpoint_id:$eid})
                WHERE NOT s:ModelEndpoint
                REMOVE s.unreviewed_provenance
            """,
                eid=eid,
            ).consume()
        # Simulate a *different owner* in-memory: must HOLD without graph write.
        altered = _read_pair(session, eid)
        altered[0]["owners"] = [harness.PREFIX + ":foreign"]
        try:
            assess_exact_synthetic_pair(altered, expected_owner=owner, expected_endpoint=eid)
            results["foreign_ownership_refuses_automatic_reconciliation"] = False
        except SyntheticCollisionHold:
            results["foreign_ownership_refuses_automatic_reconciliation"] = _aggregate(session, eid) == original
        # A nonstandard incident relationship must likewise HOLD.
        altered = _read_pair(session, eid)
        altered[0]["relationship_types"].append("UNREVIEWED_EDGE")
        try:
            assess_exact_synthetic_pair(altered, expected_owner=owner, expected_endpoint=eid)
            results["unknown_relationship_refuses_auto_reconciliation"] = False
        except SyntheticCollisionHold:
            results["unknown_relationship_refuses_auto_reconciliation"] = _aggregate(session, eid) == original

        # Real physical failure path: an unreviewed relationship attached
        # to the would-be deleted service must prevent ANY reconciliation.
        session.run(
            """
            MATCH (s:ServiceEndpoint {endpoint_id:$eid})
            WHERE NOT s:ModelEndpoint
            CREATE (s)-[:UNREVIEWED_EDGE]->(:SyntheticCheck {test_only:true})
            """,
            eid=eid,
        ).consume()
        try:
            session.execute_write(_manual_lab_reconcile, endpoint=eid, owner=owner)
            results["physical_extra_edge_refuses_reconciliation"] = False
        except SyntheticCollisionHold:
            results["physical_extra_edge_refuses_reconciliation"] = _aggregate(session, eid) == original
        finally:
            session.run(
                """
                MATCH (s:ServiceEndpoint {endpoint_id:$eid})
                      -[edge:UNREVIEWED_EDGE]->(check:SyntheticCheck)
                DELETE edge, check
                """,
                eid=eid,
            ).consume()
        # Deliberately apply the *entire* test-only destructive write
        # inside a managed transaction, then throw before commit. The
        # original two nodes/relationships and the model port must
        # survive. This does not substitute for process-kill recovery.
        try:
            session.execute_write(_fault_after_synthetic_reconcile, endpoint=eid, owner=owner)
            results["post_write_injected_failure_rolls_back_atomically"] = False
        except SyntheticCollisionHold:
            original_restored = _aggregate(session, eid) == original
            full_pair_restored = False
            try:
                assess_exact_synthetic_pair(
                    _read_pair(session, eid),
                    expected_owner=owner,
                    expected_endpoint=eid,
                )
                full_pair_restored = True
            except SyntheticCollisionHold:
                pass
            results["post_write_injected_failure_rolls_back_atomically"] = original_restored and full_pair_restored

        # Isolated graph ONLY, explicitly reviewed synthetic fixture: one
        # single transaction, no DETACH DELETE, no live data can be supplied.
        outcome = session.execute_write(_manual_lab_reconcile, endpoint=eid, owner=owner)
        results["explicit_test_only_reconciliation_preserves_edges"] = outcome[
            "production_release_authorized"
        ] is False and _aggregate(session, eid) == {"nodes": 1, "exposes": 1, "serves": 1}
        # Re-establish the exact uniqueness constraint after the trial.
        session.run("""
            CREATE CONSTRAINT canonical_service_endpoint IF NOT EXISTS
            FOR (e:ServiceEndpoint) REQUIRE e.endpoint_id IS UNIQUE
        """).consume()
        count = session.run("""
            SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties
            WHERE type='UNIQUENESS' AND labelsOrTypes=['ServiceEndpoint']
              AND properties=['endpoint_id']
            RETURN count(*) AS n
        """).single()
        results["synthetic_uniqueness_restored_after_resolution"] = count is not None and count["n"] == 1
        # Existing UNWIRED writer can now idempotently use that synthetic ID.
        same = harness.candidate.candidate_register_model_endpoint(
            session, endpoint_id=eid, node_id=owner, model_ids=(modelid,)
        )
        results["unwired_writer_accepts_reconciled_synthetic_identity"] = same[
            "production_release_authorized"
        ] is False and _aggregate(session, eid) == {"nodes": 1, "exposes": 1, "serves": 1}


BASE_SCENARIO = harness.scenario


def _combined_scenarios(driver, results):
    BASE_SCENARIO(driver, results)
    extra_scenarios(driver, results)


def main():
    # Uses parent harness's safe opt-in and cleanup; never exposes host
    # endpoint config, credentials, identifiers, or any mutation API.
    harness.scenario = _combined_scenarios
    return harness.main()


if __name__ == "__main__":
    raise SystemExit(main())
