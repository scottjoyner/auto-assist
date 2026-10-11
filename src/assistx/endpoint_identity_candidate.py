"""Unwired, fail-closed candidate for canonical model-endpoint registration.

This module is NOT called from AssistX runtime. It is a disposable-graph
acceptance experiment, not a production migration or live data repair.

Prerequisites:
* The database already enforces UNIQUE :ServiceEndpoint(endpoint_id).
* Service and model registrations agree that this identity denotes a
  model_endpoint; ordinary services are never silently retyped.
* Existing ambiguous/doubly-owned identities are rejected; never pick one.
* The legacy writers are fenced OFF before any future authorized cutover.

No delete, relationship pruning, account changes or schema DDL occur here.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class EndpointIdentityHold(ValueError):
    """Identity conflict or missing precondition; contains no sensitive IDs."""


# Deliberately static queries; the only interpolation is through parameters.
CONSTRAINT_QUERY = """
SHOW CONSTRAINTS YIELD type, entityType, labelsOrTypes, properties
RETURN type, entityType, labelsOrTypes, properties
"""
SERVICE_IDENTITY_QUERY = """
MATCH (e:ServiceEndpoint {endpoint_id:$endpoint_id})
OPTIONAL MATCH (owner:SwarmNode)-[:EXPOSES]->(e)
RETURN elementId(e) AS ref, labels(e) AS labels,
       e.service_type AS service_type, e.node_id AS node_id,
       e.model_endpoint_id AS model_endpoint_id,
       collect(DISTINCT owner.node_id) AS owners
"""
MODEL_IDENTITY_QUERY = """
MATCH (e:ModelEndpoint {model_endpoint_id:$endpoint_id})
RETURN elementId(e) AS ref, e.endpoint_id AS endpoint_id
"""
UPSERT_QUERY = """
MERGE (n:SwarmNode {node_id:$node_id})
ON CREATE SET n.created_at=datetime(), n.created_at_ts=timestamp(),
              n.status='online'
MERGE (e:ServiceEndpoint {endpoint_id:$endpoint_id})
ON CREATE SET e.created_at=datetime(), e.created_at_ts=timestamp()
SET e:ModelEndpoint
SET e.model_endpoint_id=$endpoint_id,
    e.service_type='model_endpoint',
    e.node_id=$node_id,
    e.updated_at=datetime(),
    e.updated_at_ts=timestamp()
SET e += $safe_properties
MERGE (n)-[:EXPOSES]->(e)
RETURN elementId(e) AS ref
"""
SERVES_QUERY = """
MATCH (e:ModelEndpoint:ServiceEndpoint
       {endpoint_id:$endpoint_id, model_endpoint_id:$endpoint_id})
MERGE (m:Model {model_id:$model_id})
ON CREATE SET m.created_at=datetime(), m.created_at_ts=timestamp()
MERGE (e)-[:SERVES]->(m)
RETURN count(m) AS linked
"""
# Never copy raw source properties wholesale into an identity-bearing node.
ALLOWED_PROPERTIES = frozenset({
    "provider", "purpose", "base_url", "network_preference", "auth_type",
    "status", "last_probed_at", "last_probed_at_ts", "models_json",
})


def _require_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or value != value.strip()
        or any(ord(char) < 32 for char in value)
    ):
        raise EndpointIdentityHold("missing or invalid canonical identity")
    return value


def _require_safe_properties(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EndpointIdentityHold("invalid endpoint metadata")
    if set(value) - ALLOWED_PROPERTIES:
        raise EndpointIdentityHold("endpoint metadata contains unapproved keys")
    props = dict(value)
    if any(
        v is not None and not isinstance(v, (str, int, float, bool))
        for v in props.values()
    ):
        raise EndpointIdentityHold("endpoint metadata is not a Neo4j scalar")
    return props


def _validate_preconditions(tx: Any, endpoint_id: str, node_id: str) -> None:
    schema_rows = [dict(row) for row in tx.run(CONSTRAINT_QUERY)]
    if not any(
        row.get("type") == "UNIQUENESS"
        and row.get("entityType") == "NODE"
        and row.get("labelsOrTypes") == ["ServiceEndpoint"]
        and row.get("properties") == ["endpoint_id"]
        for row in schema_rows
    ):
        raise EndpointIdentityHold("canonical uniqueness constraint unavailable")

    services = [dict(row) for row in tx.run(
        SERVICE_IDENTITY_QUERY, endpoint_id=endpoint_id,
    )]
    models = [dict(row) for row in tx.run(
        MODEL_IDENTITY_QUERY, endpoint_id=endpoint_id,
    )]
    if len(services) > 1 or len(models) > 1:
        raise EndpointIdentityHold("ambiguous existing identity")
    if models and not services:
        raise EndpointIdentityHold("model identity lacks canonical service record")
    if services and models and services[0].get("ref") != models[0].get("ref"):
        raise EndpointIdentityHold("canonical and model identity are distinct nodes")
    if services:
        row = services[0]
        if row.get("service_type") not in (None, "model_endpoint"):
            raise EndpointIdentityHold("existing identity is another service type")
        if row.get("node_id") not in (None, node_id):
            raise EndpointIdentityHold("endpoint identity is owned by another node")
        if row.get("model_endpoint_id") not in (None, endpoint_id):
            raise EndpointIdentityHold("endpoint has conflicting model identity")
        owners = row.get("owners")
        if not isinstance(owners, list) or any(
            owner is None or owner != node_id for owner in owners
        ):
            raise EndpointIdentityHold("existing endpoint graph ownership is ambiguous")
        if "ModelEndpoint" in row.get("labels", []) and not models:
            raise EndpointIdentityHold("unindexed model subtype identity")
    if models and models[0].get("endpoint_id") != endpoint_id:
        raise EndpointIdentityHold("model record carries noncanonical service ID")


def _write_candidate(
    tx: Any, endpoint_id: str, node_id: str,
    safe_properties: Mapping[str, Any], model_ids: Sequence[str],
) -> dict[str, Any]:
    _validate_preconditions(tx, endpoint_id, node_id)
    rows = list(tx.run(
        UPSERT_QUERY,
        endpoint_id=endpoint_id, node_id=node_id,
        safe_properties=dict(safe_properties),
    ))
    if len(rows) != 1 or not rows[0].get("ref"):
        raise EndpointIdentityHold("canonical writer did not return one node")
    for model_id in model_ids:
        linked = list(tx.run(
            SERVES_QUERY, endpoint_id=endpoint_id, model_id=model_id,
        ))
        if len(linked) != 1 or linked[0].get("linked") != 1:
            raise EndpointIdentityHold("model relationship registration incomplete")
    # Reporting IDs here is safe only to the authorized caller. The test
    # harness never records identifiers, and no production route calls this.
    return {
        "status": "CANDIDATE_REGISTERED_IN_ISOLATED_GRAPH_ONLY",
        "models_requested": len(model_ids),
        "destructive_relationship_pruning": False,
        "production_release_authorized": False,
    }


def candidate_register_model_endpoint(
    session: Any, *, endpoint_id: str, node_id: str,
    safe_properties: Mapping[str, Any] | None = None,
    model_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Prototype transaction only: never call with a live application session.

    The authorized caller must supply an isolated, disposable graph session.
    This module is not wired into any API, worker, or production discovery.
    """
    endpoint_id = _require_id(endpoint_id)
    node_id = _require_id(node_id)
    props = _require_safe_properties(safe_properties)
    if isinstance(model_ids, (str, bytes)) or not isinstance(model_ids, Sequence):
        raise EndpointIdentityHold("invalid model identity list")
    validated = [_require_id(model_id) for model_id in model_ids]
    if len(set(validated)) != len(validated) or len(validated) > 32:
        raise EndpointIdentityHold("duplicate or unbounded model identities")
    return session.execute_write(
        _write_candidate, endpoint_id, node_id, props, validated,
    )
