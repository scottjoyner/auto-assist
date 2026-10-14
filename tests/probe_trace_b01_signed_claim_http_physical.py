"""Opt-in isolated Neo4j + dual-signed HTTP claim gateway research fixture.

All graph/data/network effects occur in newly created disposable containers.
Gateway admits only one synthetic signed plan; production B01 remains HOLD.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from probe_trace_isolated_task_broker import (
    docker,assert_disposable,GRAPH_IMAGE,PYTHON_IMAGE)
from trace_b01_signed_reservation_gate import (
    SignedResourceGate,SignedEnvelope,SCHEMA,canonical,proof_digest)

SOURCE=Path(__file__).resolve().parent

def pub(key):
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
def query(driver, text, params=None, *, database="neo4j"):
    with driver.session(database=database) as session:
        return [dict(r) for r in session.run(text,params or {})]

def json_file(path, obj):
    path.write_text(json.dumps(obj,separators=(",",":")))
    path.chmod(0o600)

def run():
    if os.getenv("ASSISTX_B01_SIGNED_HTTP_PHYSICAL")!="1":
        raise RuntimeError("EXPLICIT_DISPOSABLE_SIGNED_HTTP_OPT_IN_REQUIRED")
    docker("image","inspect",GRAPH_IMAGE,timeout=12)
    docker("image","inspect",PYTHON_IMAGE,timeout=12)
    tag=secrets.token_hex(5)
    worker_net="assistx-b01-http-worker-"+tag
    graph_net="assistx-b01-http-graph-"+tag
    graph="assistx-b01-http-neo-"+tag
    gateway="assistx-b01-http-gateway-"+tag
    actor="assistx-b01-http-actor-"+tag
    admin="A"+secrets.token_urlsafe(26)
    writer_pass="W"+secrets.token_urlsafe(26)
    writer_user="fixture_http_claim_writer"
    bearer=secrets.token_urlsafe(32)
    task_id="fixture-task-"+tag+"-signed"
    agent_id="fixture-agent-"+tag+"-owner"
    nonce="nonce-"+tag+"-signed"
    policy=secrets.token_hex(32)
    issuer=Ed25519PrivateKey.generate()
    device=Ed25519PrivateKey.generate()
    made_networks=[]
    made_containers=[]
    started=time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="assistx-b01-http-") as temp:
            root=Path(temp)
            (root/"db").mkdir()
            (root/"anchor").mkdir()
            gate=SignedResourceGate.bootstrap(
                root/"db"/"ledger.sqlite",root/"anchor"/"checkpoint.json",
                pub(issuer),pub(device),task_id,agent_id,policy)
            when=int(time.time())
            body=dict(schema=SCHEMA,instance=gate.snapshot()["instance"],
                scope="fixture-assistx-b01",operation="claim_fixture_task",
                task_id=task_id,agent_id=agent_id,term=1,sequence=1,
                nonce=nonce,policy_sha256=policy,issued_at=when-2,
                expires_at=when+55)
            message=canonical(body)
            proof=SignedEnvelope(body,issuer.sign(message),device.sign(message))
            digest=proof_digest(proof)
            server_cfg=dict(bearer=bearer,writer_user=writer_user,
                writer_password=writer_pass,issuer_root=pub(issuer).hex(),
                device_root=pub(device).hex(),task_id=task_id,
                agent_id=agent_id,policy_sha256=policy)
            write_config=root/"server.json"
            json_file(write_config,server_cfg)
            actor_cfg=dict(gateway=gateway,bearer=bearer,
                signed=dict(body=body,issuer_signature=proof.issuer_signature.hex(),
                            device_signature=proof.device_signature.hex()),
                forged_issuer_signature=Ed25519PrivateKey.generate().sign(
                    message).hex(),
                forged_device_signature=Ed25519PrivateKey.generate().sign(
                    message).hex())
            for net in (worker_net,graph_net):
                docker("network","create","--internal","--driver","bridge",
                    "--label","assistx.b01.research=signed-http",net)
                made_networks.append(net)
            docker("run","-d","--pull","never","--name",graph,
                "--network",graph_net,"--network-alias","neo4j",
                "--memory","2300m","--cpus","1.5","--pids-limit","256",
                "--env","NEO4J_AUTH=neo4j/"+admin,
                "--env","NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
                "--env","NEO4J_server_memory_heap_initial__size=256m",
                "--env","NEO4J_server_memory_heap_max__size=512m",
                GRAPH_IMAGE,timeout=28)
            made_containers.append(graph)
            inspect=assert_disposable(graph,{graph_net})
            ip=inspect["NetworkSettings"]["Networks"][graph_net]["IPAddress"]
            if not ip:
                raise RuntimeError("DISPOSABLE_GRAPH_ADDRESS_UNAVAILABLE")
            actor_cfg["graph_ip"]=ip
            json_file(root/"actor.json",actor_cfg)
            with GraphDatabase.driver("bolt://"+ip+":7687",
                    auth=("neo4j",admin),connection_timeout=2,
                    connection_acquisition_timeout=4) as admin_driver:
                deadline=time.monotonic()+82
                while True:
                    try:
                        admin_driver.verify_connectivity()
                        if query(admin_driver,"RETURN 1 AS ok")==[{"ok":1}]:
                            break
                    except (Neo4jError,ServiceUnavailable,ValueError,OSError):
                        pass
                    if time.monotonic()>=deadline:
                        raise RuntimeError("DISPOSABLE_GRAPH_NOT_READY")
                    time.sleep(2)
                schema=[
                    "CREATE CONSTRAINT fixture_http_scope IF NOT EXISTS "
                    "FOR (g:FixtureFence) REQUIRE g.scope IS UNIQUE",
                    "CREATE CONSTRAINT fixture_http_id IF NOT EXISTS "
                    "FOR (t:FixtureTask) REQUIRE t.id IS UNIQUE",
                ]
                for command in schema:
                    query(admin_driver,command)
                role="fixture_http_writer_role"
                grants=[
                    "CREATE ROLE "+role+" IF NOT EXISTS",
                    "GRANT ACCESS ON DATABASE neo4j TO "+role,
                    "GRANT MATCH {*} ON GRAPH neo4j NODES FixtureFence, FixtureTask TO "+role,
                    "GRANT SET PROPERTY {mutex,high_water,seen_nonces} "
                    "ON GRAPH neo4j NODES FixtureFence TO "+role,
                    "GRANT SET PROPERTY {status,claimed_by,claim_nonce,claim_sequence} "
                    "ON GRAPH neo4j NODES FixtureTask TO "+role,
                    "CREATE USER "+writer_user+" SET PASSWORD '"+writer_pass+
                    "' CHANGE NOT REQUIRED",
                    "GRANT ROLE "+role+" TO "+writer_user,
                ]
                for command in grants:
                    query(admin_driver,command,database="system")
                query(admin_driver,"CREATE (:FixtureFence {scope:'fixture-assistx-b01',"
                    "term:1,high_water:0,seen_nonces:[],mutex:0,"
                    "authority_digest:$digest})",{"digest":digest})
                query(admin_driver,"CREATE (:FixtureTask {id:$id,status:'READY',"
                    "owner_id:$owner,requires_recovery_identity:true,"
                    "claimed_by:'',claim_nonce:'',claim_sequence:0})",
                    {"id":task_id,"owner":agent_id})
                docker("create","--pull","never","--name",gateway,
                    "--network",worker_net,"--memory","256m",
                    "--cpus","1","--pids-limit","128",PYTHON_IMAGE,
                    "python","-u","/tmp/gateway.py","/tmp/server.json")
                made_containers.append(gateway)
                docker("network","connect",graph_net,gateway)
                assert_disposable(gateway,{worker_net,graph_net})
                for src,dest in [
                    (root/"server.json","server.json"),
                    (root/"db","signed-gate-db"),
                    (root/"anchor","signed-gate-anchor"),
                    (SOURCE/"trace_b01_signed_claim_http_gateway.py","gateway.py"),
                    (SOURCE/"trace_b01_signed_reservation_gate.py",
                     "trace_b01_signed_reservation_gate.py"),
                    (SOURCE/"trace_b01_disposable_claim_fence.py",
                     "trace_b01_disposable_claim_fence.py"),
                ]:
                    docker("cp",str(src),gateway+":/tmp/"+dest)
                # Offline cached wheel modules; no pip, live volume or egress.
                vendors=[
                    ("/usr/lib/python3/dist-packages/cryptography","cryptography"),
                    ("/usr/local/lib/python3.12/dist-packages/neo4j","neo4j"),
                    ("/usr/lib/python3/dist-packages/"
                     "_cffi_backend.cpython-312-x86_64-linux-gnu.so",
                     "_cffi_backend.cpython-312-x86_64-linux-gnu.so"),
                ]
                for src,dest in vendors:
                    if not Path(src).exists():
                        raise RuntimeError("MISSING_CACHED_FIXTURE_DEPENDENCY")
                    docker("cp",src,gateway+":/tmp/"+dest)
                pytz=Path("/usr/lib/python3/dist-packages/pytz")
                if pytz.exists():
                    docker("cp",str(pytz),gateway+":/tmp/pytz")
                docker("start",gateway)
                docker("create","--pull","never","--name",actor,
                    "--network",worker_net,"--memory","96m",
                    "--cpus","0.5","--pids-limit","64",
                    PYTHON_IMAGE,"sleep","180")
                made_containers.append(actor)
                assert_disposable(actor,{worker_net})
                docker("cp",str(root/"actor.json"),actor+":/tmp/actor.json")
                docker("cp",str(SOURCE/"trace_b01_signed_claim_fixture_actor.py"),
                       actor+":/tmp/actor.py")
                docker("start",actor)
                deadline=time.monotonic()+30
                while True:
                    probe=docker("exec",actor,"python","-c",
                        "import urllib.request;"
                        "assert urllib.request.urlopen('http://"+gateway+
                        ":8809/health',timeout=2).status==200",
                        timeout=7,check=False)
                    if probe.returncode==0:
                        break
                    if time.monotonic()>=deadline:
                        raise RuntimeError("SIGNED_GATEWAY_UNAVAILABLE")
                    time.sleep(0.8)
                first=docker("exec",actor,"python","/tmp/actor.py",
                             "initial",timeout=25)
                first_receipt=json.loads(first.stdout.strip())
                check=query(admin_driver,"MATCH (g:FixtureFence) "
                    "RETURN g.high_water AS head")[0]
                done=query(admin_driver,"MATCH (t:FixtureTask {id:$id}) "
                    "RETURN t.status AS status,t.claimed_by AS who",
                    {"id":task_id})[0]
                if check!={"head":1} or done!={"status":"CLAIMED","who":agent_id}:
                    raise RuntimeError("SIGNED_CLAIM_NOT_ATOMICALLY_APPLIED")
                # Restart only this uniquely named disposable gateway. It
                # must reopen *existing* ledger/anchor and retain PENDING.
                docker("restart",gateway,timeout=18)
                deadline=time.monotonic()+20
                while True:
                    status=docker("exec",gateway,"python","-c",
                        "import json,sqlite3;"
                        "c=sqlite3.connect('/tmp/signed-gate-db/ledger.sqlite');"
                        "s=json.loads(c.execute('SELECT document FROM state "
                        "WHERE id=1').fetchone()[0]);"
                        "assert s['high_water']==1 and s['pending'] is not None",
                        timeout=7,check=False)
                    if status.returncode==0:
                        break
                    if time.monotonic()>=deadline:
                        raise RuntimeError("DISPOSABLE_PENDING_LOST_ON_RESTART")
                    time.sleep(0.5)
                # Graph-only restore is distinct from independent gateway
                # ledger; a previous valid signed proof remains consumed.
                query(admin_driver,"MATCH (g:FixtureFence) "
                    "SET g.high_water=0,g.seen_nonces=[]")
                query(admin_driver,"MATCH (t:FixtureTask {id:$id}) "
                    "SET t.status='READY',t.claimed_by='',"
                    "t.claim_nonce='',t.claim_sequence=0",{"id":task_id})
                later=docker("exec",actor,"python","/tmp/actor.py",
                             "after_graph_restore",timeout=18)
                later_receipt=json.loads(later.stdout.strip())
                if query(admin_driver,"MATCH (t:FixtureTask {id:$id}) "
                    "RETURN t.status AS status",{"id":task_id})!=[{"status":"READY"}]:
                    raise RuntimeError("REPLAY_ALTERED_RESTORED_GRAPH")
                # Deliberate security NEGATIVE: the restricted graph writer
                # can still issue unreviewed Cypher bypassing the gateway.
                with GraphDatabase.driver("bolt://"+ip+":7687",
                        auth=(writer_user,writer_pass),
                        connection_timeout=3) as compromised:
                    bypass=query(compromised,
                        "MATCH (t:FixtureTask {id:$id}) "
                        "SET t.status='CLAIMED' RETURN t.status AS status",
                        {"id":task_id})
                if bypass!=[{"status":"CLAIMED"}]:
                    raise RuntimeError("EXPECTED_WRITER_CYPHER_BYPASS_NOT_SHOWN")
                head=query(admin_driver,"MATCH (g:FixtureFence) "
                    "RETURN g.high_water AS head")[0]["head"]
                if head!=0:
                    raise RuntimeError("WRITER_BYPASS_DID_NOT_SKIP_FENCE")
                return dict(
                    schema="assistx-b01-signed-http-effect-v0",
                    verdict="PASS_SYNTHETIC_WITH_WRITER_BYPASS_NO_GO",
                    actual_signed_http_claim=True,
                    bearer_theft_cannot_forge_signatures=True,
                    raw_digest_and_query_augmentation_denied=True,
                    direct_actor_graph_tcp_denied=True,
                    broker_only_actor_network=True,
                    graph_only_restore_replay_denied=True,
                    pending_reservation_survived_disposable_gateway_restart=True,
                    compromised_graph_writer_bypass_reproduced=True,
                    graph_native_ed25519_enforced=False,
                    truly_independent_highwater_proven=False,
                    production_claim_authorized=False,
                    production_graph_queried=False,
                    host_published_graph_ports=False,
                    actor_initial=first_receipt,actor_replay=later_receipt,
                    elapsed_seconds=round(time.monotonic()-started,2))
    finally:
        for name in reversed(made_containers):
            docker("rm","-f","-v",name,timeout=27,check=False)
        for name in reversed(made_networks):
            docker("network","rm",name,timeout=26,check=False)

if __name__=="__main__":
    print(json.dumps(run(),sort_keys=True))