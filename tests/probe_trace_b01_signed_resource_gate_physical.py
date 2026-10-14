"""Opt-in physical Neo4j + off-graph signed reservation, synthetic ONLY.

Does NOT prove an uncompromisable resource: direct Neo4j writer bypass and
coordinated restore of both local ledger and checkpoint remain NO-GO.
Never connects to production graph, networks, volumes or service credentials.
"""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import json
import os
import secrets
import shutil
import tempfile
import time
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from probe_trace_isolated_task_broker import docker, GRAPH_IMAGE
from trace_b01_disposable_claim_fence import FixtureClaim,try_claim
from trace_b01_signed_reservation_gate import (
    SCHEMA, SignedEnvelope, SignedResourceGate,AdmissionDenied,
    canonical,proof_digest,
)

def public(key):
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
def graph_query(driver, statement, values=None):
    with driver.session(database="neo4j") as session:
        return [dict(row) for row in session.run(statement,values or {})]

def run():
    if os.getenv("ASSISTX_B01_DUAL_SIGNATURE_PHYSICAL")!="1":
        raise RuntimeError("EXPLICIT_SIGNED_DISPOSABLE_OPT_IN_REQUIRED")
    docker("image","inspect",GRAPH_IMAGE,timeout=12)
    tag=secrets.token_hex(5)
    network="assistx-b01-signed-private-"+tag
    graph="assistx-b01-signed-graph-"+tag
    admin="A"+secrets.token_urlsafe(27)
    writer_pass="W"+secrets.token_urlsafe(27)
    issuer=Ed25519PrivateKey.generate()
    device=Ed25519PrivateKey.generate()
    task_id="fixture-task-"+tag+"-claim"
    agent_id="fixture-agent-"+tag+"-signed"
    nonce="nonce-"+tag+"-admission"
    policy_sha256=secrets.token_hex(32)
    made_network=False
    made_graph=False
    start=time.monotonic()
    try:
        docker("network","create","--internal","--driver","bridge",
               "--label","assistx.b01.research=signed-reservation",
               network)
        made_network=True
        docker("run","-d","--pull","never","--name",graph,
               "--network",network,"--memory","2300m",
               "--cpus","1.5","--pids-limit","256",
               "--env","NEO4J_AUTH=neo4j/"+admin,
               "--env","NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
               "--env","NEO4J_server_memory_heap_initial__size=256m",
               "--env","NEO4J_server_memory_heap_max__size=512m",
               GRAPH_IMAGE,timeout=28)
        made_graph=True
        inspect=json.loads(docker("inspect",graph).stdout)[0]
        if (set(inspect["NetworkSettings"]["Networks"])!={network}
                or inspect["HostConfig"].get("PortBindings")
                or inspect["HostConfig"].get("Binds")
                or any(x["Type"]=="bind" for x in inspect.get("Mounts",[]))):
            raise RuntimeError("UNSAFE_DISPOSABLE_GRAPH_TOPOLOGY")
        ip=inspect["NetworkSettings"]["Networks"][network]["IPAddress"]
        if not ip:
            raise RuntimeError("MISSING_DISPOSABLE_GRAPH_ADDRESS")
        uri="bolt://"+ip+":7687"
        with GraphDatabase.driver(uri,auth=("neo4j",admin),
               connection_timeout=2,connection_acquisition_timeout=4) as root:
            deadline=time.monotonic()+85
            while True:
                try:
                    root.verify_connectivity()
                    if graph_query(root,"RETURN 1 AS ok")==[{"ok":1}]:
                        break
                except (Neo4jError,ServiceUnavailable,OSError,ValueError):
                    pass
                if time.monotonic()>deadline:
                    raise RuntimeError("DISPOSABLE_GRAPH_READY_TIMEOUT")
                time.sleep(2)
            # Synthetic fixture roots are generated here and never stored in
            # the disposable graph. This is NOT independently held key custody.
            with tempfile.TemporaryDirectory(
                    prefix="assistx-b01-signed-research-") as temp:
                base=Path(temp)
                (base/"ledger").mkdir()
                (base/"checkpoint").mkdir()
                db=base/"ledger"/"resource.sqlite"
                anchor=base/"checkpoint"/"resource.json"
                gate=SignedResourceGate.bootstrap(
                    db,anchor,public(issuer),public(device),
                    task_id,agent_id,policy_sha256)
                now=int(time.time())
                body={"schema":SCHEMA,
                      "instance":gate.snapshot()["instance"],
                      "scope":"fixture-assistx-b01",
                      "operation":"claim_fixture_task",
                      "task_id":task_id,"agent_id":agent_id,
                      "term":1,"sequence":1,"nonce":nonce,
                      "policy_sha256":policy_sha256,
                      "issued_at":now-3,"expires_at":now+45}
                payload=canonical(body)
                envelope=SignedEnvelope(
                    body,issuer.sign(payload),device.sign(payload))
                digest=proof_digest(envelope)
                forged=replace(envelope,
                    issuer_signature=Ed25519PrivateKey.generate().sign(payload))
                try:
                    gate.reserve(forged,now=now)
                    raise RuntimeError("FORGED_DUAL_SIGNATURE_ACCEPTED")
                except AdmissionDenied:
                    pass
                bad_body=SignedEnvelope(
                    {**body,"agent_id":"fixture-agent-malicious123"},
                    envelope.issuer_signature,envelope.device_signature)
                try:
                    gate.reserve(bad_body,now=now)
                    raise RuntimeError("TAMPERED_SIGNED_PRINCIPAL_ACCEPTED")
                except AdmissionDenied:
                    pass
                # Fixture-only Neo4j writer grant. Arbitrary allowed Cypher
                # with this credential is still demonstrably unsafe.
                role="fixture_signed_writer"
                user="fixture_signed_writer"
                syscmd=[
                    "CREATE ROLE "+role+" IF NOT EXISTS",
                    "GRANT ACCESS ON DATABASE neo4j TO "+role,
                    "GRANT MATCH {*} ON GRAPH neo4j NODES FixtureFence, FixtureTask TO "+role,
                    ("GRANT SET PROPERTY {mutex,high_water,seen_nonces} "
                     "ON GRAPH neo4j NODES FixtureFence TO "+role),
                    ("GRANT SET PROPERTY {status,claimed_by,claim_nonce,claim_sequence} "
                     "ON GRAPH neo4j NODES FixtureTask TO "+role),
                    ("CREATE USER "+user+" SET PASSWORD '"+writer_pass
                     +"' CHANGE NOT REQUIRED"),
                    "GRANT ROLE "+role+" TO "+user,
                ]
                graph_query(root,"CREATE CONSTRAINT fixture_signed_scope "
                            "IF NOT EXISTS FOR (g:FixtureFence) REQUIRE g.scope IS UNIQUE")
                graph_query(root,"CREATE CONSTRAINT fixture_signed_task "
                            "IF NOT EXISTS FOR (t:FixtureTask) REQUIRE t.id IS UNIQUE")
                for command in syscmd:
                    with root.session(database="system") as session:
                        session.run(command).consume()
                graph_query(root,"CREATE (:FixtureFence {"
                            "scope:'fixture-assistx-b01',term:1,"
                            "high_water:0,seen_nonces:[],mutex:0,"
                            "authority_digest:$digest})",{"digest":digest})
                graph_query(root,"CREATE (:FixtureTask {id:$id,status:'READY',"
                            "owner_id:$agent,requires_recovery_identity:true,"
                            "claimed_by:'',claim_nonce:'',claim_sequence:0})",
                            {"id":task_id,"agent":agent_id})
                db_before=base/"ledger-before.sqlite"
                shutil.copy2(db,db_before)
                ticket=gate.reserve(envelope,now=now)
                if (ticket["proof_sha256"]!=digest or
                        ticket["state"]!="PENDING_UNCERTAIN" or
                        ticket["graph_write_authorized"] is not False):
                    raise RuntimeError("INVALID_SIGNED_RESERVATION_TICKET")
                with GraphDatabase.driver(uri,auth=(user,writer_pass),
                        connection_timeout=3) as writer:
                    claim=FixtureClaim(task_id,agent_id,1,1,nonce,digest,True)
                    actual=try_claim(writer,claim)
                    if not actual or actual["status"]!="CLAIMED":
                        raise RuntimeError("SIGNED_GRAPH_CAS_FAILED")
                    try:
                        gate.reserve(envelope,now=now)
                        raise RuntimeError("RESOURCE_LEDGER_REPLAY_NOT_DENIED")
                    except AdmissionDenied:
                        pass
                    if gate.read_only_reconcile()["state"]!="PENDING_UNCERTAIN":
                        raise RuntimeError("UNCERTAIN_INFLIGHT_NOT_PRESERVED")
                    # Rewind only synthetic graph. Resource ledger remains
                    # monotonic, but a leaked graph writer STILL bypasses it.
                    graph_query(root,"MATCH (g:FixtureFence) SET "
                                "g.high_water=0,g.seen_nonces=[]")
                    graph_query(root,"MATCH (t:FixtureTask) SET "
                                "t.status='READY',t.claim_nonce='',"
                                "t.claim_sequence=0,t.claimed_by=''")
                    try:
                        gate.reserve(envelope,now=now)
                        raise RuntimeError("GRAPH_RESTORE_REPLAY_ACCEPTED_AT_GATE")
                    except AdmissionDenied:
                        pass
                    bypass=try_claim(writer,claim)
                    if not bypass or bypass["status"]!="CLAIMED":
                        raise RuntimeError("EXPECTED_WRITER_BYPASS_NOT_REPRODUCED")
                # Independent local anchor catches a DB-only rewind.
                shutil.copy2(db_before,db)
                try:
                    gate.snapshot()
                    raise RuntimeError("LOCAL_DB_RESTORE_NOT_DETECTED")
                except AdmissionDenied:
                    pass
                return {
                    "schema":"assistx-b01-physical-dual-signed-gate-v0",
                    "verdict":"PASS_SYNTHETIC_WITH_RESOURCE_NO_GO",
                    "dual_signed_reservation_before_graph_write":True,
                    "forged_issuer_and_principal_tamper_rejected":True,
                    "graph_only_restore_replay_denied_by_external_ledger":True,
                    "db_only_restore_mismatch_detected":True,
                    "uncertain_inflight_remains_pending":True,
                    "arbitrary_writer_bypass_still_reproduced":True,
                    "coordinated_restore_not_defended":True,
                    "independent_hardware_trust_roots_proven":False,
                    "neo4j_native_signature_enforcement_proven":False,
                    "production_graph_queried":False,
                    "production_admission_authorized":False,
                    "host_published_ports":False,
                    "elapsed_seconds":round(time.monotonic()-start,2)}
    finally:
        if made_graph:
            docker("rm","-f","-v",graph,timeout=27,check=False)
        if made_network:
            docker("network","rm",network,timeout=25,check=False)

if __name__=="__main__":
    print(json.dumps(run(),sort_keys=True))