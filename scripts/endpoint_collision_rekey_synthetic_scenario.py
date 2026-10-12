"""Disposable-graph ONLY model/service identity deconfliction acceptance.

No production caller, API route, reusable migration CLI, data export, or
operator-approved policy exists here. All writes are performed solely by the
parent guarded --run-disposable Neo4j harness with synthetic IDs.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class SyntheticRekeyHold(ValueError):
    """Fail closed without leaking endpoint identifiers or properties."""


_INSPECT = """
MATCH (e:ServiceEndpoint {endpoint_id:$endpoint_id})
OPTIONAL MATCH (n:SwarmNode)-[:EXPOSES]->(e)
WITH e, collect(DISTINCT n.node_id) AS owners
RETURN elementId(e) AS ref, labels(e) AS labels,
       e.node_id AS node_id, e.service_type AS service_type,
       e.model_endpoint_id AS model_id, owners,
       count { (e)-[:SERVES]->(:Model) } AS serves_count,
       count { (:SwarmNode)-[:EXPOSES]->(e) } AS exposes_count
"""
_NEW_ID = """
MATCH (e:ServiceEndpoint {endpoint_id:$endpoint_id})
RETURN count(e) AS n
"""
_REKEY_ONE = """
MATCH (e:ServiceEndpoint)
WHERE elementId(e)=$node_ref
  AND e.endpoint_id=$old_endpoint_id
  AND NOT e:ModelEndpoint
SET e.endpoint_id=$new_endpoint_id
RETURN count(e) AS n
"""


def _synthetic(value: object) -> str:
    if (
        type(value) is not str
        or not value.startswith("scratch-acceptance-")
        or not 0 < len(value) <= 120
        or any(ch.isspace() for ch in value)
    ):
        raise SyntheticRekeyHold("non-synthetic or invalid endpoint identifier")
    return value


def synthetic_rekey_in_transaction(
    tx: Any, *, old_id: str, new_id: str, owner_id: str, approved: bool,
) -> None:
    """A highly bounded *synthetic* proof; NOT a production data migration.

    Does not delete nodes, detach/prune relationships, alter service/model type,
    or copy properties. It cannot safely execute with concurrent legacy writers.
    """
    old_id = _synthetic(old_id)
    new_id = _synthetic(new_id)
    owner_id = _synthetic(owner_id)
    if approved is not True or old_id == new_id:
        raise SyntheticRekeyHold("no explicit isolated-graph approval")
    rows = [dict(row) for row in tx.run(_INSPECT, endpoint_id=old_id)]
    if len(rows) != 2:
        raise SyntheticRekeyHold("synthetic duplicate group has unexpected size")
    legacy = [
        r for r in rows
        if r.get("labels") == ["ServiceEndpoint"]
        and r.get("service_type") == "generic"
        and r.get("model_id") is None
        and r.get("serves_count") == 0
    ]
    models = [
        r for r in rows
        if set(r.get("labels", [])) == {"ServiceEndpoint", "ModelEndpoint"}
        and r.get("service_type") == "model_endpoint"
        and r.get("model_id") == old_id
        and r.get("serves_count") == 1
    ]
    if len(legacy) != 1 or len(models) != 1:
        raise SyntheticRekeyHold("ambiguous service and model identity")
    for row in rows:
        if (
            row.get("node_id") != owner_id
            or row.get("owners") != [owner_id]
            or row.get("exposes_count") != 1
            or not isinstance(row.get("ref"), str)
        ):
            raise SyntheticRekeyHold("ownership or relationship provenance unverified")
    target = tx.run(_NEW_ID, endpoint_id=new_id).single()
    if target is None or target.get("n") != 0:
        raise SyntheticRekeyHold("new synthetic identity already occupied")
    result = tx.run(
        _REKEY_ONE,
        node_ref=legacy[0]["ref"],
        old_endpoint_id=old_id,
        new_endpoint_id=new_id,
    ).single()
    if result is None or result.get("n") != 1:
        raise SyntheticRekeyHold("synthetic identity rekey incomplete")


def synthetic_rekey_scenarios(driver: Any, candidate: Any, prefix: str) -> dict[str, bool]:
    """Run only from the parent isolated Docker harness after existing scenarios."""
    if not prefix.startswith("scratch-acceptance-"):
        raise SyntheticRekeyHold("disposable synthetic prefix required")
    old, renamed, owner = (
        prefix + ":duplicate-service-model",
        prefix + ":ordinary-service-new-id",
        prefix + ":rekey-owner",
    )
    alternate = prefix + ":foreign-owner"
    occupied = prefix + ":occupied-new-id"
    result: dict[str, bool] = {}
    with driver.session(database="neo4j") as session:
        # The parent already proved the constraint. Drop it ONLY in this
        # disposable graph to reproduce the actual pre-constraint collision.
        session.run("DROP CONSTRAINT canonical_service_endpoint IF EXISTS").consume()
        session.run("""
        CREATE (n:SwarmNode {node_id:$owner})
        CREATE (s:ServiceEndpoint {
            endpoint_id:$old, service_type:'generic', node_id:$owner,
            port:4242, status:'synthetic'
        })
        CREATE (e:ServiceEndpoint:ModelEndpoint {
            endpoint_id:$old, model_endpoint_id:$old,
            service_type:'model_endpoint', node_id:$owner,
            provider:'synthetic'
        })
        CREATE (m:Model {model_id:$model})
        CREATE (n)-[:EXPOSES]->(s)
        CREATE (n)-[:EXPOSES]->(e)
        CREATE (e)-[:SERVES]->(m)
        """, owner=owner, old=old, model=prefix + ":test-model").consume()
        result["synthetic_duplicate_group_exactly_two"] = (
            session.run(
                "MATCH (e:ServiceEndpoint {endpoint_id:$old}) RETURN count(e) AS n",
                old=old,
            ).single()["n"] == 2
        )
        # A uniqueness constraint cannot be installed over this duplicate.
        try:
            session.run("""
            CREATE CONSTRAINT canonical_service_endpoint IF NOT EXISTS
            FOR (e:ServiceEndpoint) REQUIRE e.endpoint_id IS UNIQUE
            """).consume()
            result["duplicate_blocks_constraint_before_repair"] = False
        except Exception:
            # Error details are never emitted; only a negative status boolean.
            result["duplicate_blocks_constraint_before_repair"] = True
        session.run("""
        CREATE (:ServiceEndpoint {endpoint_id:$occupied, service_type:'synthetic'})
        """, occupied=occupied).consume()

        def refused(*, target: str, target_owner: str, approved: bool) -> bool:
            try:
                session.execute_write(
                    synthetic_rekey_in_transaction,
                    old_id=old, new_id=target, owner_id=target_owner, approved=approved,
                )
            except SyntheticRekeyHold:
                return True
            return False

        result["requires_explicit_synthetic_owner_approval"] = refused(
            target=renamed, target_owner=owner, approved=False,
        )
        result["foreign_owner_fails_before_write"] = refused(
            target=renamed, target_owner=alternate, approved=True,
        )
        result["preexisting_target_id_fails_before_write"] = refused(
            target=occupied, target_owner=owner, approved=True,
        )
        result["denied_cases_do_not_mutate_duplicate_group"] = (
            session.run(
                "MATCH (e:ServiceEndpoint {endpoint_id:$old}) RETURN count(e) AS n",
                old=old,
            ).single()["n"] == 2
        )

        # Explicit approval affects only this purely synthetic disposable
        # graph; production requires independent policy/consumer signoff.
        session.execute_write(
            synthetic_rekey_in_transaction,
            old_id=old, new_id=renamed, owner_id=owner, approved=True,
        )
        edges = session.run("""
        MATCH (n:SwarmNode {node_id:$owner})
        OPTIONAL MATCH (n)-[:EXPOSES]->(e:ServiceEndpoint)
        WHERE e.endpoint_id IN [$old,$new]
        WITH count(DISTINCT e) AS exposed
        MATCH (m:ModelEndpoint:ServiceEndpoint {endpoint_id:$old})
        RETURN exposed,
               count { (m)-[:SERVES]->(:Model) } AS served
        """, owner=owner, old=old, new=renamed).single()
        result["both_exposes_edges_and_serves_edge_preserved"] = (
            edges is not None
            and edges["exposed"] == 2 and edges["served"] == 1
        )
        records = list(session.run("""
        MATCH (e:ServiceEndpoint)
        WHERE e.endpoint_id IN [$old,$new]
        RETURN e.endpoint_id=$old AS old_key,
               e:ModelEndpoint AS is_model,
               e.service_type AS kind,
               e.port AS port,
               e.provider AS provider
        """, old=old, new=renamed))
        result["two_nodes_retained_with_distinct_unmodified_types"] = (
            len(records) == 2
            and any(r["old_key"] and r["is_model"]
                    and r["kind"] == "model_endpoint"
                    and r["provider"] == "synthetic" for r in records)
            and any(not r["old_key"] and not r["is_model"]
                    and r["kind"] == "generic" and r["port"] == 4242 for r in records)
        )
        session.run("""
        CREATE CONSTRAINT canonical_service_endpoint IF NOT EXISTS
        FOR (e:ServiceEndpoint) REQUIRE e.endpoint_id IS UNIQUE
        """).consume()
        result["uniqueness_constraint_accepted_after_synthetic_rekey"] = (
            session.run("""
            SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties
            WHERE type='UNIQUENESS'
              AND labelsOrTypes=['ServiceEndpoint']
              AND properties=['endpoint_id']
            RETURN count(*) AS n
            """).single()["n"] == 1
        )
        try:
            a = candidate.candidate_register_model_endpoint(
                session, endpoint_id=old, node_id=owner,
                model_ids=(prefix + ":test-model",),
            )
            b = candidate.candidate_register_service_endpoint(
                session, endpoint_id=renamed, node_id=owner,
                service_type="generic", safe_properties={"port": 4242},
            )
            result["both_canonical_writers_reaccept_preserved_nodes"] = (
                not a["production_release_authorized"]
                and not b["production_release_authorized"]
                and session.run("""
                MATCH (e:ServiceEndpoint)
                WHERE e.endpoint_id IN [$old,$new]
                RETURN count(e) AS n
                """, old=old, new=renamed).single()["n"] == 2
            )
        except candidate.EndpointIdentityHold:
            result["both_canonical_writers_reaccept_preserved_nodes"] = False

        # RED TEAM NEGATIVE — reproduce only in the disposable graph.
        # The *current* legacy swarm_core service registration still MERGEs
        # on endpoint_id and blindly SETs service properties. A stale event
        # carrying the pre-rekey ordinary-service ID now matches the *model*
        # endpoint, even though uniqueness has been installed.
        #
        # Never run this against production. This intentionally corrupts one
        # synthetic test node to prove migration requires legacy-writer
        # fencing, not merely a new uniqueness constraint.
        session.run("""
        MATCH (n:SwarmNode {node_id:$node_id})
        MERGE (e:ServiceEndpoint {endpoint_id:$endpoint_id})
        ON CREATE SET e.created_at=datetime(), e.created_at_ts=timestamp()
        SET e += $props, e.updated_at=datetime(), e.updated_at_ts=timestamp()
        MERGE (n)-[:EXPOSES]->(e)
        """, node_id=owner, endpoint_id=old,
             props={"service_type": "generic", "status": "synthetic"}).consume()

        redteam = session.run("""
        MATCH (model:ModelEndpoint:ServiceEndpoint {endpoint_id:$old})
        MATCH (service:ServiceEndpoint {endpoint_id:$new})
        RETURN model.service_type AS model_kind,
               service.service_type AS service_kind,
               count { (model)-[:SERVES]->(:Model) } AS model_links,
               count { (:SwarmNode)-[:EXPOSES]->(model) } AS model_owners,
               count { (:SwarmNode)-[:EXPOSES]->(service) } AS service_owners
        """, old=old, new=renamed).single()
        result["stale_legacy_service_registration_corrupts_model_kind_even_after_uniqueness"] = (
            redteam is not None
            and redteam["model_kind"] == "generic"
            and redteam["service_kind"] == "generic"
            and redteam["model_links"] == 1
            and redteam["model_owners"] == 1
            and redteam["service_owners"] == 1
        )

        # The new canonical model writer must fail closed after the stale
        # writer's semantic corruption; it does not silently fix bad history.
        try:
            candidate.candidate_register_model_endpoint(
                session, endpoint_id=old, node_id=owner,
                model_ids=(prefix + ":test-model",),
            )
            result["canonical_writer_denies_legacy_semantic_corruption"] = False
        except candidate.EndpointIdentityHold:
            result["canonical_writer_denies_legacy_semantic_corruption"] = True
    return result
