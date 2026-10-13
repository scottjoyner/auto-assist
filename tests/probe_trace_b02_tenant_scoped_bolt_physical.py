"""Opt-in synthetic Neo4j Bolt tenant scope test; no production graph access.
Only create/delete uniquely named internal-only disposable graph and network.
This intentionally demonstrates why RBAC alone cannot stop unscoped Cypher.
"""
from __future__ import annotations
import json
import os
import secrets
import time
from neo4j import GraphDatabase, READ_ACCESS
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from probe_trace_isolated_task_broker import docker, GRAPH_IMAGE as GRAPH
from trace_b02_tenant_scoped_candidate import (
    TENANT_CYPHER, TenantDenied, authorize_fixture,
    tenant_params, safe_tenant_rows)
from fastapi.security import HTTPBasicCredentials

def read(driver, statement: str, params: dict | None = None, *, database="neo4j"):
    with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        return [dict(x) for x in session.run(statement, params or {})]

def write(driver, statement: str, *, database="neo4j"):
    with driver.session(database=database) as session:
        return [dict(x) for x in session.run(statement)]

def assert_isolated(graph, network):
    info=json.loads(docker("inspect", graph).stdout)[0]
    if (set(info["NetworkSettings"]["Networks"])!={network}
        or info["HostConfig"].get("PortBindings")
        or info["HostConfig"].get("Binds")):
        raise RuntimeError("UNSAFE_GRAPH_TOPOLOGY")
    if any(m.get("Type")!="volume"
           or m.get("Destination") not in {"/data", "/logs"}
           or len(str(m.get("Name", ""))) < 50
           for m in info.get("Mounts", [])):
        raise RuntimeError("NONANONYMOUS_GRAPH_MOUNT")
    return info["NetworkSettings"]["Networks"][network]["IPAddress"]

def run():
    if os.getenv("ASSISTX_B02_TENANT_BOLT")!="1":
        raise RuntimeError("EXPLICIT_DISPOSABLE_TENANT_BOLT_OPT_IN_REQUIRED")
    docker("image","inspect",GRAPH,timeout=10)
    nonce=secrets.token_hex(5)
    network="assistx-b02-tenant-only-"+nonce
    graph="assistx-b02-tenant-graph-"+nonce
    admin="A"+secrets.token_urlsafe(26)
    reader_pass="R"+secrets.token_urlsafe(26)
    role="fixture_tenant_reader"
    user="fixture_tenant_reader"
    created_net=False
    created_graph=False
    began=time.monotonic()
    try:
        docker("network","create","--internal","--driver","bridge",
               "--label","assistx.b02.research=tenant-fixture",network)
        created_net=True
        docker("run","-d","--pull","never","--name",graph,
               "--network",network,"--memory","2300m","--cpus","1.5",
               "--pids-limit","256",
               "--env","NEO4J_AUTH=neo4j/"+admin,
               "--env","NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
               "--env","NEO4J_server_memory_heap_initial__size=256m",
               "--env","NEO4J_server_memory_heap_max__size=512m",
               GRAPH,timeout=28)
        created_graph=True
        ip=assert_isolated(graph,network)
        if not ip:
            raise RuntimeError("MISSING_DISPOSABLE_BOLT_ADDRESS")
        with GraphDatabase.driver("bolt://"+ip+":7687",
                auth=("neo4j",admin),connection_timeout=2,
                connection_acquisition_timeout=3) as root:
            deadline=time.monotonic()+80
            while True:
                try:
                    root.verify_connectivity()
                    if read(root,"RETURN 1 AS ok")==[{"ok":1}]:
                        break
                except (Neo4jError,ServiceUnavailable,ValueError,OSError):
                    pass
                if time.monotonic()>=deadline:
                    raise RuntimeError("DISPOSABLE_GRAPH_NOT_READY")
                time.sleep(2)
            cmds=[
                "CREATE ROLE "+role+" IF NOT EXISTS",
                "GRANT ACCESS ON DATABASE neo4j TO "+role,
                ("GRANT MATCH {tenant_id,id,kind,title,status,target_agent_id,"
                 "payload_json,updated_at_ts,summary,last_error,created_at_ts} "
                 "ON GRAPH neo4j NODES Task TO "+role),
                "CREATE USER "+user+" SET PASSWORD '"+reader_pass+"' CHANGE NOT REQUIRED",
                "GRANT ROLE "+role+" TO "+user,
            ]
            for stmt in cmds:
                write(root,stmt,database="system")
            payload='{"suite_id":"suite-1","chain":{"chain_id":"chain-1"},'
            payload+='"api_key":"SYNTHETIC-PRIVATE"}'
            for tenant,task_id in (("tenant-a","fixture-a1"),
                                   ("tenant-a","fixture-a2"),
                                   ("tenant-b","fixture-b1")):
                with root.session() as session:
                    session.run(
                        "CREATE (:Task {tenant_id:$tenant,id:$id,"
                        "kind:'harness_reflect',status:'READY',"
                        "title:'SYNTHETIC-PRIVATE-TITLE',"
                        "summary:'SYNTHETIC-PRIVATE-SUMMARY',"
                        "last_error:'SYNTHETIC-PRIVATE-ERROR',"
                        "target_agent_id:'agent-1',"
                        "updated_at_ts:1,created_at_ts:1,"
                        "payload_json:$payload,secret_token:'SYNTHETIC-PRIVATE'})",
                        tenant=tenant,id=task_id,payload=payload).consume()
            write(root,"CREATE (:Task {id:'fixture-no-tenant',"
                 "kind:'harness_reflect',status:'READY',created_at_ts:1})")
            scope=authorize_fixture(
                principal="fixture-operator",
                credentials=HTTPBasicCredentials(username="fixture-operator",
                                                 password="synthetic-pass"),
                configured_user="fixture-operator",
                configured_password="synthetic-pass",
                tenant_bindings={"fixture-operator":"tenant-a"},
                explicit_offline=True)
            with GraphDatabase.driver("bolt://"+ip+":7687",
                    auth=(user,reader_pass),connection_timeout=3) as limited:
                output=read(limited,TENANT_CYPHER,
                            tenant_params(scope,status="READY"))
                rows=[r["task"] for r in output]
                safe=safe_tenant_rows(scope,rows)
                if {r["id"] for r in safe}!={"fixture-a1","fixture-a2"}:
                    raise RuntimeError("TENANT_READ_SET_MISMATCH")
                if "SYNTHETIC-PRIVATE" in json.dumps(safe):
                    raise RuntimeError("PRIVACY_MINIMIZATION_LEAK")
                unscoped=read(limited,"MATCH (t:Task) RETURN t.id AS id")
                unscoped_ids={r["id"] for r in unscoped}
                if not {"fixture-b1","fixture-no-tenant"}.issubset(unscoped_ids):
                    raise RuntimeError("EXPECTED_DB_ROLE_BYPASS_NOT_OBSERVED")
                rejected=False
                try:
                    foreign=read(limited,TENANT_CYPHER,
                                 {"tenant_id":"tenant-b","status":"READY","limit":40})
                    safe_tenant_rows(scope,[r["task"] for r in foreign])
                except TenantDenied:
                    rejected=True
                if not rejected:
                    raise RuntimeError("FOREIGN_ROW_NOT_REJECTED")
                mutation_denied=False
                try:
                    write(limited,"CREATE (:Task {id:'forbidden'})")
                except Neo4jError:
                    mutation_denied=True
                if not mutation_denied:
                    raise RuntimeError("READ_ROLE_CAN_WRITE")
            return dict(schema="assistx-b02-tenant-physical-v0",
                        verdict="PASS_SYNTHETIC_ONLY",
                        selected_scope_task_count=len(safe),
                        selected_scope_ids_match=True,
                        missing_tenant_excluded_by_fixed_query=True,
                        privacy_free_text_removed=True,
                        foreign_response_rejected=True,
                        restricted_role_write_denied=mutation_denied,
                        arbitrary_unscoped_cypher_still_reads_foreign_tenant=True,
                        db_role_alone_is_not_tenant_boundary=True,
                        broker_must_restrict_to_fixed_parameterized_plan=True,
                        graph_internal_network_only=True,
                        host_published_ports=False,
                        production_graph_queried=False,
                        production_cutover_authorized=False,
                        elapsed_seconds=round(time.monotonic()-began,2))
    finally:
        if created_graph:
            docker("rm","-f","-v",graph,timeout=27,check=False)
        if created_net:
            docker("network","rm",network,timeout=27,check=False)

if __name__=="__main__":
    print(json.dumps(run(),sort_keys=True))