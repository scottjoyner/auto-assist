"""Opt-in counts-only Neo4j read canary (operator console, not HTTP).
Execute from a running AssistX API container with Python stdin:
  docker exec -i -e ASSISTX_B02_COUNTS_READONLY=1 assistx-api python3 - < SCRIPT
The production container's existing Neo4j environment stays inside it.
Prints fixed categories and aggregate counts, never Task values or credentials.
"""
from __future__ import annotations
import json
import os
import sys

PLAN_ID = "harness_evolution_aggregate_counts_research_v1"
KINDS = ("harness_reflect", "harness_train", "harness_deploy",
         "harness_rescore", "adaptive_model_benchmark")
STATUSES = ("READY", "CLAIMED", "RUNNING", "DONE", "FAILED", "ERROR")
QUERY = (
    "MATCH (t:Task) WHERE t.kind IN $kinds AND t.status IN $statuses "
    "RETURN t.kind AS kind, t.status AS status, count(t) AS n ORDER BY kind, status"
)

def run() -> dict:
    if os.environ.get("ASSISTX_B02_COUNTS_READONLY") != "1":
        raise ValueError("EXPLICIT_OPERATOR_OPT_IN_REQUIRED")
    names = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE")
    if not all(os.environ.get(n) for n in names):
        raise ValueError("EXISTING_NEO4J_CONFIG_REQUIRED")
    from neo4j import GraphDatabase, READ_ACCESS
    from pathlib import Path
    # The same self-contained probe is executed via stdin inside the container.
    # The source-pinned allowlists are independently checked in local unit tests.
    driver=GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"],os.environ["NEO4J_PASSWORD"]),
        connection_timeout=2,max_connection_pool_size=1)
    try:
        with driver.session(database=os.environ["NEO4J_DATABASE"],
                            default_access_mode=READ_ACCESS) as session:
            rows=[dict(record) for record in session.run(
                QUERY,{"kinds":list(KINDS),"statuses":list(STATUSES)},timeout=3)]
    finally:
        driver.close()
    cells={}
    if len(rows) > len(KINDS)*len(STATUSES):
        raise ValueError("TOO_MANY_AGGREGATE_ROWS")
    for row in rows:
        if type(row) is not dict or set(row)!={"kind","status","n"}:
            raise ValueError("UNEXPECTED_READ_FIELDS")
        kind,status,n=row["kind"],row["status"],row["n"]
        if (type(kind) is not str or kind not in KINDS
                or type(status) is not str or status not in STATUSES
                or type(n) is not int or n < 0 or n > 100_000_000
                or (kind,status) in cells):
            raise ValueError("UNEXPECTED_AGGREGATE_VALUE")
        cells[(kind,status)] = n
    return {
        "schema":"assistx-b02-live-counts-canary-v1",
        "plan":PLAN_ID,
        "matched_task_count":sum(cells.values()),
        "kind_counts":{kind:sum(cells.get((kind,status),0) for status in STATUSES)
                       for kind in KINDS},
        "status_counts":{status:sum(cells.get((kind,status),0) for kind in KINDS)
                         for status in STATUSES},
        "number_of_nonzero_cells":len(cells),
        "raw_task_values_emitted":False,
        "production_graph_writes":False,
        "production_route_enabled":False,
        "global_operator_policy_approved":False,
        "production_shadow_authorized":False,
        "b01_closed":False,"b06_closed":False,
    }

if __name__=="__main__":
    try:
        print(json.dumps(run(),sort_keys=True))
    except Exception as exc:
        print(json.dumps({"schema":"assistx-b02-live-counts-canary-v1",
            "result":"UNVERIFIED","failure_class":type(exc).__name__,
            "production_shadow_authorized":False},sort_keys=True))
        sys.exit(2)
