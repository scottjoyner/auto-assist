"""Disposable *physical Bolt* verification of B02 harness read candidate.
Read-only to fleet; writes exclusively into the synthetic disposable Neo4j instance.
Never connects to a running Neo4j/AssistX, publishes ports or retains graph data.
"""
from __future__ import annotations
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys
import time

# Standalone opt-in runner needs checked-out AssistX pure auth helper.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from trace_b02_harness_projection_candidate import (
    CYPHER, CYPHER_TENANT_SCOPED, KEYS, TENANT_PLAN_ID, params,
    validate_broker_rows, run_shadow_parity)
from fastapi.security import HTTPBasicCredentials
from trace_b02_tenant_broker_boundary import (
    BrokerBoundaryDenied, ServerOwnedDirectory, resolve_scoped_read)
from trace_b02_harness_counts_canary import (
    QUERY as COUNTS_QUERY, fixed_params as counts_fixed_params,
    validate_rows as validate_counts_rows)
from probe_trace_isolated_task_broker import docker, GRAPH_IMAGE as GRAPH

def assert_isolated(graph, network):
    info=json.loads(docker("inspect",graph).stdout)[0]
    if (set(info["NetworkSettings"]["Networks"])!={network}
            or info["HostConfig"].get("PortBindings")
            or info["HostConfig"].get("Binds")):
        raise RuntimeError("DISPOSABLE_BOLT_NETWORK_OR_PORT_UNSAFE")
    mounts=info.get("Mounts",[])
    if (any(m.get("Type")!="volume"
            or m.get("Destination") not in {"/data","/logs"}
            or len(str(m.get("Name",""))) < 50 for m in mounts)):
        raise RuntimeError("DISPOSABLE_BOLT_PERSISTENT_MOUNT_UNSAFE")
    # Only auto-created anonymous Docker image volumes are allowed.
    # All are removed with this fixture's docker rm -fv.
    return True

def execute(driver, cypher, *, database="neo4j", data=None):
    with driver.session(database=database) as session:
        return [dict(row) for row in session.run(cypher, data or {})]

def run():
    if os.environ.get("ASSISTX_B02_DISPOSABLE_BOLT")!="1":
        raise RuntimeError("EXPLICIT_DISPOSABLE_BOLT_OPT_IN_REQUIRED")
    docker("image","inspect",GRAPH,timeout=10)
    suffix=secrets.token_hex(5)
    net="assistx-b02-bolt-only-"+suffix
    graph="assistx-b02-neo-bolt-"+suffix
    admin="A"+secrets.token_urlsafe(28)
    reader_password="R"+secrets.token_urlsafe(28)
    reader_user="fixture_harness_reader"
    net_created=False
    graph_created=False
    start=time.monotonic()
    try:
        docker("network","create","--internal","--driver","bridge",
               "--label","assistx.b02.research=harness-bolt",net)
        net_created=True
        docker("run","-d","--pull","never","--name",graph,
               "--network",net,"--network-alias","b02-fixture-graph",
               "--memory","2300m","--cpus","1.5","--pids-limit","256",
               "--env","NEO4J_AUTH=neo4j/"+admin,
               "--env","NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
               "--env","NEO4J_server_memory_heap_initial__size=256m",
               "--env","NEO4J_server_memory_heap_max__size=512m",GRAPH,
               timeout=28)
        graph_created=True
        info=json.loads(docker("inspect",graph).stdout)[0]
        assert_isolated(graph,net)
        ip=info["NetworkSettings"]["Networks"][net]["IPAddress"]
        if not ip:
            raise RuntimeError("DISPOSABLE_GRAPH_IP_MISSING")
        uri="bolt://"+ip+":7687"
        failures=Counter()
        with GraphDatabase.driver(uri,auth=("neo4j",admin),
                connection_timeout=2,connection_acquisition_timeout=3) as root:
            deadline=time.monotonic()+78
            while True:
                try:
                    root.verify_connectivity()
                    if execute(root,"RETURN 1 AS ok")==[{"ok":1}]:
                        break
                except (ServiceUnavailable,Neo4jError,OSError,ValueError,RuntimeError) as exc:
                    failures[type(exc).__name__]+=1
                if time.monotonic()>=deadline:
                    raise RuntimeError("DISPOSABLE_BOLT_START_FAILED:"+repr(dict(failures)))
                time.sleep(2)
            system=[
                "CREATE ROLE fixture_harness_role IF NOT EXISTS",
                "GRANT ACCESS ON DATABASE neo4j TO fixture_harness_role",
                ("GRANT MATCH {id,kind,title,status,target_agent_id,payload_json,"
                 "updated_at_ts,summary,last_error,created_at_ts,tenant_id} "
                 "ON GRAPH neo4j NODES Task TO fixture_harness_role"),
                ("CREATE USER "+reader_user+" SET PASSWORD '"+reader_password+
                 "' CHANGE NOT REQUIRED"),
                "GRANT ROLE fixture_harness_role TO "+reader_user,
            ]
            for stmt in system:
                execute(root,stmt,database="system")
            data=[
                """UNWIND range(1,45) AS i CREATE (:Task {
 id:'fixture-'+toString(i),kind:'harness_reflect',
 title:'Objective '+toString(i),status:'READY',target_agent_id:'agent-A',
 payload_json:'{"suite_id":"fixture","chain":{"chain_id":"chain-1"}}',
 updated_at_ts:i,created_at_ts:i,tenant_id:'synthetic-primary-tenant',
 summary:'summary '+toString(i),
 last_error:'',secret_token:'UNAUTHORIZED_FIXTURE_VALUE'})""",
                """CREATE (:Task {id:'fixture-ERROR',kind:'harness_deploy',
 status:'ERROR',created_at_ts:100,updated_at_ts:101,title:'Failed demo',
 secret_token:'UNAUTHORIZED_FIXTURE_VALUE'})"""
            ]
            for stmt in data:
                execute(root,stmt)
            # Extract the legacy result through the fixture admin, never production.
            old=execute(root,
                "MATCH (t:Task {status:$status}) RETURN t AS task "
                "ORDER BY t.created_at_ts DESC LIMIT $limit",
                data=params("READY"))
            old_rows=[dict(record["task"]) for record in old]
            if len(old_rows)!=40:
                raise RuntimeError("ADMIN_LEGACY_REFERENCE_INCOMPLETE")
            with GraphDatabase.driver(uri,auth=(reader_user,reader_password),
                    connection_timeout=3) as scoped:
                result=execute(scoped,CYPHER,data=params("READY"))
                projected=validate_broker_rows([x["task"] for x in result])
                if len(projected)!=40:
                    raise RuntimeError("RESTRICTED_READ_COUNT_MISMATCH")
                if [x["id"] for x in projected]!=[
                        "fixture-"+str(i) for i in range(45,5,-1)]:
                    raise RuntimeError("RESTRICTED_SORT_OR_LIMIT_MISMATCH")
                other=execute(scoped,CYPHER,data=params("ERROR"))
                one=validate_broker_rows([x["task"] for x in other])
                if len(one)!=1 or one[0]["payload_json"] is not None:
                    raise RuntimeError("NULL_PROPERTY_PROJECTION_CHANGED")
                denied={}
                for name,stmt in {
                    "create":"CREATE (:Task {id:'never-allowed'})",
                    "set":"MATCH (t:Task {id:'fixture-45'}) SET t.status='DONE'",
                    "delete":"MATCH (t:Task {id:'fixture-45'}) DELETE t",
                }.items():
                    try:
                        execute(scoped,stmt)
                    except Neo4jError:
                        denied[name]=True
                    else:
                        denied[name]=False
                    if not denied[name]:
                        raise RuntimeError("B02_WRITER_GRANT_LEAK:"+name)
                secret_inaccessible=False
                try:
                    check=execute(scoped,
                        "MATCH (t:Task {id:'fixture-45'}) RETURN t.secret_token AS secret")
                    secret_inaccessible=(not check or all(
                        x.get("secret")!="UNAUTHORIZED_FIXTURE_VALUE" for x in check))
                except Neo4jError:
                    secret_inaccessible=True
                if not secret_inaccessible:
                    raise RuntimeError("SENSITIVE_GRAPH_PROPERTY_EXPOSED")
                # Validate selected property maps against the legacy reference,
                # including the exact status/limit semantics and Python view.
                for raw, limited in zip(old_rows,projected):
                    if {k:raw.get(k) for k in KEYS}!=limited:
                        raise RuntimeError("LEGACY_FIELD_PARITY_DIFF")
                view=Path(__file__).resolve().parents[1] / "src/assistx/harness_views.py"
                spec=importlib.util.spec_from_file_location("b02_snapshot",view)
                module=importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if not run_shadow_parity(runs=[],raw_tasks=old_rows,
                                         snapshot_fn=module.harness_evolution_snapshot):
                    raise RuntimeError("HARNESS_VIEW_PARITY_DIFF")
                # Intentional negative: this status-only research query lacks
                # a tenant predicate. A *synthetic* second-tenant row is
                # visible to the same restricted reader. No real tenant data.
                execute(root, """CREATE (:Task {id:'fixture-other-scope',
                    status:'READY',kind:'harness_reflect',
                    title:'Other synthetic scope',created_at_ts:9000,
                    tenant_id:'synthetic-other-tenant'})""")
                mixed=execute(scoped,CYPHER,data=params("READY"))
                status_only_cross_scope=any(
                    row["task"].get("id")=="fixture-other-scope"
                    for row in mixed)
                if not status_only_cross_scope:
                    raise RuntimeError("EXPECTED_UNSCOPED_QUERY_COUNTEREXAMPLE_MISSING")
                # Fake broker authority: caller chooses only plan + status,
                # not a tenant. Source is a synthetic server-owned directory,
                # not a validated production principal/tenant issuer.
                directory=ServerOwnedDirectory(
                    revision="fixture-scope-rev-1",
                    principal_tenants={
                        "fixture-owner-a":"synthetic-primary-tenant",
                        "fixture-owner-b":"synthetic-other-tenant"})
                def intent_for(principal):
                    return resolve_scoped_read(
                        principal=principal,
                        credentials=HTTPBasicCredentials(
                            username=principal,password="offline-only"),
                        configured_user=principal,
                        configured_password="offline-only",
                        allowed_basic_operators=principal,
                        server_owned_directory=directory,
                        request={"plan_id":TENANT_PLAN_ID,"status":"READY"})
                first=intent_for("fixture-owner-a")
                second=intent_for("fixture-owner-b")
                scoped_primary=execute(
                    scoped,first.cypher,data=first.parameters)
                scoped_other=execute(
                    scoped,second.cypher,data=second.parameters)
                spoofed_scope_denied=False
                try:
                    resolve_scoped_read(
                        principal="fixture-owner-a",
                        credentials=HTTPBasicCredentials(
                            username="fixture-owner-a",password="offline-only"),
                        configured_user="fixture-owner-a",
                        configured_password="offline-only",
                        allowed_basic_operators="fixture-owner-a",
                        server_owned_directory=directory,
                        request={"plan_id":TENANT_PLAN_ID,"status":"READY",
                                 "tenant_id":"synthetic-other-tenant"})
                except BrokerBoundaryDenied:
                    spoofed_scope_denied=True
                if not spoofed_scope_denied:
                    raise RuntimeError("TENANT_SCOPE_OVERRIDE_NOT_DENIED")
                scoped_primary_ids=[x["task"]["id"] for x in scoped_primary]
                scoped_other_ids=[x["task"]["id"] for x in scoped_other]
                scoped_predicate_test=(
                    len(scoped_primary_ids)==40
                    and "fixture-other-scope" not in scoped_primary_ids
                    and scoped_other_ids==["fixture-other-scope"])
                if not scoped_predicate_test:
                    raise RuntimeError("TENANT_PREDICATE_PHYSICAL_ISOLATION_FAILED")
                aggregate=validate_counts_rows(execute(
                    scoped,COUNTS_QUERY,data=counts_fixed_params()))
                if (aggregate["matching_task_count"]!=47
                        or aggregate["kind_counts"]["harness_reflect"]!=46
                        or aggregate["kind_counts"]["harness_deploy"]!=1
                        or aggregate["status_counts"]["READY"]!=46
                        or aggregate["status_counts"]["ERROR"]!=1
                        or aggregate["contains_task_values"]):
                    raise RuntimeError("B02_AGGREGATE_FIXED_PLAN_PARITY_FAILED")
            return {
                "schema":"assistx-b02-neo4j-bolt-physical-v1",
                "result":"PASS","disposable_graph_image":GRAPH,
                "physical_neo4j_enterprise_role_verification":True,
                "only_disposable_graph_modified":True,
                "internal_only_network":True,
                "host_published_ports":False,"persistent_graph_mounts":False,
                "plan":"harness_evolution_tasks_read_v1",
                "row_count":len(projected),"sorted_limit_40":True,
                "missing_task_fields_null":True,"selected_fields_match_legacy":True,
                "legacy_harness_view_parity":True,
                "reader_denied_mutations":denied,
                "secret_property_inaccessible":secret_inaccessible,
                "status_only_synthetic_cross_scope_negative_reproduced":status_only_cross_scope,
                "tenant_scoped_synthetic_predicate_pass":scoped_predicate_test,
                "synthetic_broker_scope_override_denied":spoofed_scope_denied,
                "synthetic_principal_to_directory_query_parity":True,
                "counts_only_fixed_aggregate_physical_pass":True,
                "counts_only_synthetic_fixture_task_count":aggregate["matching_task_count"],
                "tenant_scoped_query_principal_binding_proven":False,
                "production_task_tenant_property_parity_proven":False,
                "tenant_isolation_proven":False,
                "global_operator_scope_reviewed":False,
                "elapsed_seconds":round(time.monotonic()-start,2),
                "production_graph_queried":False,
                "production_cutover_authorized":False,
                "B01_closed":False,"B06_closed":False,
            }
    finally:
        if graph_created:
            docker("rm","-f","-v",graph,timeout=27,check=False)
        if net_created:
            docker("network","rm",net,timeout=27,check=False)

if __name__=="__main__":
    print(json.dumps(run(),sort_keys=True))
