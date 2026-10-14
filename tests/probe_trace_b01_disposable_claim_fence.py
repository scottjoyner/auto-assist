"""Opt-in disposable Neo4j fixture for single-resource claim high-water CAS.

Only a new uniquely named internal Docker graph/network; no host ports,
bind mounts, production graph, network or service changes. Includes expected
NO-GO bypass and rollback negatives; never claims production admission.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
import secrets
import time
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from probe_trace_isolated_task_broker import docker, GRAPH_IMAGE as GRAPH
from trace_b01_disposable_claim_fence import FixtureClaim, FencedDenied, try_claim

def statement(driver, cypher, parameters=None, *, database="neo4j"):
    with driver.session(database=database) as session:
        return [dict(r) for r in session.run(cypher, parameters or {})]

def snapshot(driver):
    guard=statement(driver,"MATCH (g:FixtureFence) RETURN g.term AS term,"
                    "g.high_water AS high_water, g.seen_nonces AS seen_nonces,"
                    "g.mutex AS mutex")[0]
    tasks=statement(driver,"MATCH (t:FixtureTask) RETURN t.id AS id,"
                    "t.status AS status,t.claim_sequence AS seq ORDER BY id")
    return {"guard":guard,"tasks":tasks}
def run():
    if os.getenv("ASSISTX_B01_DISPOSABLE_CLAIM_FENCE")!="1":
        raise RuntimeError("EXPLICIT_DISPOSABLE_FENCE_OPT_IN_REQUIRED")
    docker("image","inspect",GRAPH,timeout=12)
    tag=secrets.token_hex(5)
    network="assistx-b01-claim-only-"+tag
    graph="assistx-b01-claim-graph-"+tag
    admin="A"+secrets.token_urlsafe(27)
    writer_password="W"+secrets.token_urlsafe(27)
    writer_user="fixture_claim_writer"
    writer_role="fixture_claim_role"
    digest=secrets.token_hex(32)
    network_created=False
    graph_created=False
    start=time.monotonic()
    try:
        docker("network","create","--internal","--driver","bridge",
               "--label","assistx.b01.research=claim-fence",network)
        network_created=True
        docker("run","-d","--pull","never","--name",graph,
               "--network",network,"--memory","2300m",
               "--cpus","1.5","--pids-limit","256",
               "--env","NEO4J_AUTH=neo4j/"+admin,
               "--env","NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
               "--env","NEO4J_server_memory_heap_initial__size=256m",
               "--env","NEO4J_server_memory_heap_max__size=512m",
               GRAPH,timeout=28)
        graph_created=True
        inspect=json.loads(docker("inspect",graph).stdout)[0]
        if (set(inspect["NetworkSettings"]["Networks"])!={network}
                or inspect["HostConfig"].get("PortBindings")
                or inspect["HostConfig"].get("Binds")
                or any(m.get("Type")=="bind" for m in inspect.get("Mounts",[]))):
            raise RuntimeError("UNSAFE_DISPOSABLE_GRAPH")
        ip=inspect["NetworkSettings"]["Networks"][network]["IPAddress"]
        if not ip:
            raise RuntimeError("DISPOSABLE_GRAPH_HAS_NO_IP")
        uri="bolt://"+ip+":7687"
        with GraphDatabase.driver(uri,auth=("neo4j",admin),
                                  connection_timeout=2,
                                  connection_acquisition_timeout=3) as root:
            deadline=time.monotonic()+78
            while True:
                try:
                    root.verify_connectivity()
                    if statement(root,"RETURN 1 AS ok")==[{"ok":1}]:
                        break
                except (Neo4jError,ServiceUnavailable,OSError,ValueError):
                    pass
                if time.monotonic()>=deadline:
                    raise RuntimeError("DISPOSABLE_FENCE_GRAPH_NOT_READY")
                time.sleep(2)
            setup=[
                "CREATE CONSTRAINT fixture_fence_scope IF NOT EXISTS "
                "FOR (g:FixtureFence) REQUIRE g.scope IS UNIQUE",
                "CREATE CONSTRAINT fixture_task_id IF NOT EXISTS "
                "FOR (t:FixtureTask) REQUIRE t.id IS UNIQUE",
                "CREATE ROLE "+writer_role+" IF NOT EXISTS",
                "GRANT ACCESS ON DATABASE neo4j TO "+writer_role,
                "GRANT MATCH {*} ON GRAPH neo4j NODES FixtureFence, FixtureTask TO "+writer_role,
                ("GRANT SET PROPERTY {mutex,high_water,seen_nonces} "
                 "ON GRAPH neo4j NODES FixtureFence TO "+writer_role),
                ("GRANT SET PROPERTY {status,claimed_by,claim_nonce,claim_sequence} "
                 "ON GRAPH neo4j NODES FixtureTask TO "+writer_role),
                ("CREATE USER "+writer_user+" SET PASSWORD '"
                 +writer_password+"' CHANGE NOT REQUIRED"),
                "GRANT ROLE "+writer_role+" TO "+writer_user,
            ]
            for command in setup[:2]:
                statement(root,command)
            for command in setup[2:]:
                statement(root,command,database="system")
            statement(root,"CREATE (:FixtureFence {scope:'fixture-assistx-b01',"
                          "term:1,high_water:0,seen_nonces:[],mutex:0,"
                          "authority_digest:$digest})",{"digest":digest})
            agent="fixture-agent-"+tag
            tasks=["fixture-task-"+tag+"-"+n for n in (
                "a","b","c","raceA","raceB","restored","bypass")]
            for i,task in enumerate(tasks):
                statement(root,"CREATE (:FixtureTask {id:$id,status:'READY',"
                          "owner_id:$owner,requires_recovery_identity:$recovery,"
                          "claimed_by:'',claim_nonce:'',claim_sequence:0})",
                          {"id":task,"owner":agent,"recovery":i<3})
            def claim(task, seq, nonce, term=1, verified=True):
                return FixtureClaim(task_id=task,agent_id=agent,term=term,
                                    sequence=seq,nonce=nonce,
                                    authority_digest=digest,
                                    recovery_identity_verified=verified)
            nonce=lambda n:"nonce-"+tag+"-"+n
            with GraphDatabase.driver(uri,auth=(writer_user,writer_password),
                       connection_timeout=3,connection_acquisition_timeout=5) as w:
                def state():
                    return snapshot(root)
                valid=try_claim(w,claim(tasks[0],1,nonce("first")))
                if not valid or valid["high_water"] != 1:
                    raise RuntimeError("VALID_FENCED_CLAIM_NOT_ADMITTED")
                baseline=state()
                negatives=[
                    ("stale_term",claim(tasks[1],2,nonce("old"),term=0)),
                    ("replayed_sequence",claim(tasks[1],1,nonce("seqreplay"))),
                    ("replayed_nonce",claim(tasks[1],2,nonce("first"))),
                    ("wrong_agent",replace(claim(tasks[1],2,nonce("agent")),
                                           agent_id="fixture-agent-unauthorized")),
                    ("recovery_identity",claim(tasks[1],2,nonce("node"),verified=False)),
                    ("wrong_digest",replace(claim(tasks[1],2,nonce("digest")),
                                            authority_digest="b"*64)),
                    ("already_claimed",claim(tasks[0],2,nonce("repeat"))),
                ]
                denied={}
                for label,intent in negatives:
                    try:
                        result=try_claim(w,intent)
                    except FencedDenied:
                        if label != "stale_term":
                            raise
                        result=None  # invalid zero term rejected before graph
                    if result is not None or state()!=baseline:
                        raise RuntimeError("FENCE_DENIAL_MUTATED_GRAPH:"+label)
                    denied[label]=True
                second=try_claim(w,claim(tasks[1],2,nonce("second")))
                if not second or second["high_water"]!=2:
                    raise RuntimeError("SECOND_VALID_CLAIM_DENIED")
                if try_claim(w,claim(tasks[2],3,nonce("premature"),term=2)) is not None:
                    raise RuntimeError("UNISSUED_TERM_ACCEPTED")
                # Only disposable admin raises authority term; this is NOT
                # an independently signed/attested production authority.
                statement(root,"MATCH (g:FixtureFence) SET g.term=2")
                if try_claim(w,claim(tasks[2],3,nonce("stale"),term=1)) is not None:
                    raise RuntimeError("PREDECESSOR_TERM_ACCEPTED")
                third=try_claim(w,claim(tasks[2],3,nonce("third"),term=2))
                if not third or third["term"]!=2:
                    raise RuntimeError("CURRENT_TERM_DENIED")
                # Same guard, same next sequence, different Task nodes.
                # Two racing claims must not both mutate their Task.
                args=[claim(tasks[3],4,nonce("raceone"),term=2),
                      claim(tasks[4],4,nonce("racetwo"),term=2)]
                with ThreadPoolExecutor(max_workers=2) as pool:
                    winners=list(pool.map(lambda c:try_claim(w,c),args))
                if sum(x is not None for x in winners)!=1:
                    raise RuntimeError("CONCURRENT_DOUBLE_CLAIM_ADMITTED")
                after_race=state()
                if after_race["guard"]["high_water"]!=4:
                    raise RuntimeError("HIGH_WATER_NOT_AT_FOUR")
                # Privileged writer bypass negative: arbitrary Cypher with
                # same set-property grant can change Task without fence.
                bypass=statement(w,"MATCH (t:FixtureTask {id:$id}) "
                               "SET t.status='CLAIMED' RETURN t.status AS status",
                               {"id":tasks[6]})
                if bypass!=[{"status":"CLAIMED"}]:
                    raise RuntimeError("EXPECTED_WRITER_BYPASS_NOT_OBSERVED")
                if state()["guard"]["high_water"]!=4:
                    raise RuntimeError("BYPASS_MUST_NOT_INCREMENT_FENCE")
                # Simulate an administrative restore that rewinds Graph-only
                # high-water / nonce history. The *old* proof becomes valid.
                # This is an intentionally reproduced NO-GO, not recovery.
                statement(root,"MATCH (g:FixtureFence) SET g.term=1,"
                               "g.high_water=0,g.seen_nonces=[]")
                rewind=try_claim(w,claim(tasks[5],1,nonce("first")))
                if rewind is None:
                    raise RuntimeError("EXPECTED_ROLLBACK_REPLAY_NOT_REPRODUCED")
                return {
                    "schema":"assistx-b01-disposable-resource-claim-v0",
                    "verdict":"PASS_SYNTHETIC_WITH_PRODUCTION_NO_GO",
                    "valid_current_term_claim":True,
                    "denied_mutations_leave_graph_unchanged":True,
                    "stale_replay_owner_and_recovery_denied":denied,
                    "term_turnover_rejects_old_term":True,
                    "competing_same_sequence_exactly_one_won":True,
                    "arbitrary_writer_cypher_bypasses_fixed_statement":True,
                    "graph_only_restore_reaccepts_old_proof":True,
                    "production_authority_signature_verified":False,
                    "independent_rollback_anchor_proven":False,
                    "production_task_claim_cutover_authorized":False,
                    "production_graph_queried":False,
                    "host_published_ports":False,
                    "elapsed_seconds":round(time.monotonic()-start,2)}
    finally:
        if graph_created:
            docker("rm","-f","-v",graph,timeout=28,check=False)
        if network_created:
            docker("network","rm",network,timeout=25,check=False)

if __name__=="__main__":
    print(json.dumps(run(),sort_keys=True))