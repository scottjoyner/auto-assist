"""B02 read-only *metadata-only* Task ownership preflight.

Explicit operator opt-in required; intended to run inside the running AssistX
API container using its existing Neo4j configuration. It never prints URI,
credentials, Task property values, IDs, titles, payloads, or tenant identities.
The three statements are fixed, bounded/read-only, with a 3-second query timeout.
This is NOT permission to stage or migrate production Tasks.
"""
from __future__ import annotations

import json
import os

PROBE_SCHEMA = "assistx-b02-task-ownership-metadata-v1"
REQUIRED_OWNER_KEYS = frozenset(("tenant_id", "owner_user", "created_by"))
METADATA_QUERY = (
    "CALL db.propertyKeys() YIELD propertyKey "
    "WHERE propertyKey IN ['tenant_id','owner_user','created_by'] "
    "RETURN collect(propertyKey) AS keys_found"
)
NODE_COUNT_QUERY = "MATCH (t:Task) RETURN count(t) AS task_nodes_total"
SAMPLE_QUERY = (
    "MATCH (t:Task) WITH t LIMIT $limit "
    "RETURN count(t) AS sample_count, "
    "count(t.payload_json) AS sample_payload_present"
)
SAMPLE_LIMIT = 100


class PreflightDenied(ValueError):
    pass


def _counts(keys_found: object, total: object, sample: object,
            sample_payload_present: object) -> dict:
    if (type(keys_found) is not list or
            any(type(key) is not str or key not in REQUIRED_OWNER_KEYS
                for key in keys_found) or
            len(set(keys_found)) != len(keys_found)):
        raise PreflightDenied("UNTRUSTED_PROPERTY_CATALOG")
    if (type(total) is not int or total < 0
            or type(sample) is not int or sample < 0
            or sample > min(SAMPLE_LIMIT, total)
            or type(sample_payload_present) is not int
            or not 0 <= sample_payload_present <= sample):
        raise PreflightDenied("INVALID_METADATA_COUNTERS")
    found = sorted(keys_found)
    return {
        "schema": PROBE_SCHEMA,
        "verdict": "HOLD_NO_AUTHORITATIVE_TASK_OWNERSHIP",
        "task_nodes_total": total,
        "sample_limit": SAMPLE_LIMIT,
        "sample_count": sample,
        "sample_payload_present": sample_payload_present,
        "ownership_property_keys_in_catalog": found,
        "all_expected_ownership_keys_absent_from_catalog": len(found) == 0,
        "tenant_scoped_shadow_authorized": False,
        "global_operator_shadow_authorized": False,
        "backfill_authorized": False,
        "production_cutover_authorized": False,
        "b01_graph_network_isolation_accepted": False,
        "b06_signed_closure_accepted": False,
        "raw_task_data_emitted": False,
        "credential_values_emitted": False,
    }


def read_only_preflight() -> dict:
    if os.environ.get("ASSISTX_B02_SCHEMA_READONLY") != "1":
        raise PreflightDenied("EXPLICIT_OPERATOR_OPT_IN_REQUIRED")
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER")
    password = os.environ.get("NEO4J_PASSWORD")
    database = os.environ.get("NEO4J_DATABASE")
    if not all((uri, user, password, database)):
        raise PreflightDenied("EXISTING_NEO4J_RUNTIME_CONFIG_REQUIRED")
    # A fixed explicit READ_ACCESS session plus read-only Cypher statements.
    # No route is added and no database role/credential is changed.
    from neo4j import GraphDatabase, READ_ACCESS
    driver = GraphDatabase.driver(
        uri, auth=(user, password),
        max_connection_pool_size=1, connection_timeout=2)
    try:
        with driver.session(database=database,
                            default_access_mode=READ_ACCESS) as session:
            key_record = session.run(METADATA_QUERY, timeout=3).single()
            total_record = session.run(NODE_COUNT_QUERY, timeout=3).single()
            sample_record = session.run(
                SAMPLE_QUERY, {"limit": SAMPLE_LIMIT}, timeout=3).single()
            if not key_record or not total_record or not sample_record:
                raise PreflightDenied("INCOMPLETE_GRAPH_METADATA_RECEIPT")
            return _counts(
                key_record["keys_found"], total_record["task_nodes_total"],
                sample_record["sample_count"],
                sample_record["sample_payload_present"])
    finally:
        driver.close()


if __name__ == "__main__":
    try:
        print(json.dumps(read_only_preflight(), sort_keys=True))
    except Exception as exc:
        # Do not leak database endpoint, creds, Neo4j query/Task content,
        # or underlying driver error messages into evidence/report channels.
        print(json.dumps({
            "schema": PROBE_SCHEMA, "verdict": "UNVERIFIED",
            "failure_class": type(exc).__name__,
            "tenant_scoped_shadow_authorized": False,
            "production_cutover_authorized": False,
        }, sort_keys=True))
        raise SystemExit(2) from None
