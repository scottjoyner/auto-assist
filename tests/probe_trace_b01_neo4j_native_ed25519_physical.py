"""Isolated Neo4j 5.26 Enterprise native-Java Ed25519 effect research.

Fixture procedure is compiled inside a cached disposable Neo4j JDK image,
loaded into a new graph only, never installed on the production Neo4j host.
Caller credential receives ONLY EXECUTE+BOOSTED EXECUTE on the exact
procedure, not SET/CREATE/DELETE. Admin/host compromise and graph-only
restoration remain explicit NO-GO negatives.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from probe_trace_isolated_task_broker import (
    docker, assert_disposable, GRAPH_IMAGE)
from trace_b01_native_claim_protocol import params, CALL

SOURCE=Path(__file__).resolve().parent/'native_b01'/'FixtureSignedClaim.java'

def public(key):
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)

def query(driver, statement, values=None, *, database='neo4j'):
    with driver.session(database=database) as session:
        return [dict(row) for row in session.run(statement, values or {})]
def expect_rejected(label, run, state, before):
    try:
        run()
    except (Neo4jError,ValueError) as exc:
        if isinstance(exc,Neo4jError) and not (
                'SYNTHETIC_ADMISSION_DENIED' in str(exc)
                or 'Forbidden' in exc.code
                or 'AccessMode' in exc.code
                or 'Unauthorized' in exc.code):
            raise RuntimeError('UNEXPECTED_NATIVE_DENIAL:'+label) from exc
        if state()!=before:
            raise RuntimeError('DENIED_MUTATION_CHANGED_GRAPH:'+label)
        return True
    raise RuntimeError('UNAUTHORIZED_NATIVE_OPERATION_ACCEPTED:'+label)

def make_jar(temp):
    shutil.copy2(SOURCE, temp/'FixtureSignedClaim.java')
    (temp/'classes').mkdir()
    common=[
        '--rm','--pull','never','--network','none',
        '--user',str(os.getuid())+':'+str(os.getgid()),
        '--memory','1300m','--cpus','1',
        '--mount','type=bind,src='+str(temp)+',dst=/work',
    ]
    docker('run',*common,'--entrypoint','/opt/java/openjdk/bin/javac',
        GRAPH_IMAGE,'-proc:none','-cp','/var/lib/neo4j/lib/*',
        '-d','/work/classes','/work/FixtureSignedClaim.java',timeout=35)
    docker('run',*common,'--entrypoint','/opt/java/openjdk/bin/jar',
        GRAPH_IMAGE,'--create','--file','/work/b01-fixture.jar',
        '-C','/work/classes','b01research',timeout=25)
    if not (temp/'b01-fixture.jar').is_file():
        raise RuntimeError('DISPOSABLE_NATIVE_MODULE_NOT_COMPILED')
    return temp/'b01-fixture.jar'
def run():
    if os.getenv('ASSISTX_B01_NATIVE_CRYPTO_PHYSICAL')!='1':
        raise RuntimeError('EXPLICIT_DISPOSABLE_NATIVE_OPT_IN_REQUIRED')
    docker('image','inspect',GRAPH_IMAGE,timeout=12)
    tag=secrets.token_hex(5)
    graph='assistx-b01-native-neo-'+tag
    net='assistx-b01-native-net-'+tag
    admin='A'+secrets.token_urlsafe(27)
    role='fixture_native_claim_role'
    user='fixture_native_claim_user'
    password='W'+secrets.token_urlsafe(27)
    taskids=['fixture-task-'+tag+'-'+x for x in ('a','b','c','d')]
    agent='fixture-agent-'+tag+'-owner'
    instance=secrets.token_hex(32)
    policy=secrets.token_hex(32)
    issuer=Ed25519PrivateKey.generate()
    device=Ed25519PrivateKey.generate()
    other_issuer=Ed25519PrivateKey.generate()
    other_device=Ed25519PrivateKey.generate()
    created_graph=False
    created_net=False
    started=time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='assistx-b01-native-lab-') as temp:
            jar=make_jar(Path(temp))
            docker('network','create','--internal','--driver','bridge',
                '--label','assistx.b01.research=native-crypto',net)
            created_net=True
            docker('create','--pull','never','--name',graph,
                '--network',net,'--memory','2350m','--cpus','1.5',
                '--pids-limit','256',
                '--env','NEO4J_AUTH=neo4j/'+admin,
                '--env','NEO4J_ACCEPT_LICENSE_AGREEMENT=yes',
                '--env','NEO4J_server_memory_heap_initial__size=256m',
                '--env','NEO4J_server_memory_heap_max__size=512m',
                '--env','NEO4J_dbms_security_procedures_allowlist=b01research.*',
                '--env','B01_FIXTURE_ISSUER_ROOT='+public(issuer).hex(),
                '--env','B01_FIXTURE_DEVICE_ROOT='+public(device).hex(),
                GRAPH_IMAGE,timeout=27)
            created_graph=True
            docker('cp',str(jar),graph+':/var/lib/neo4j/plugins/b01-fixture.jar')
            docker('start',graph,timeout=27)
            info=assert_disposable(graph,{net})
            ip=info['NetworkSettings']['Networks'][net]['IPAddress']
            if not ip:
                raise RuntimeError('DISPOSABLE_GRAPH_IP_MISSING')
            uri='bolt://'+ip+':7687'
            with GraphDatabase.driver(uri,auth=('neo4j',admin),
                    connection_timeout=2,connection_acquisition_timeout=4) as root:
                deadline=time.monotonic()+90
                while True:
                    try:
                        root.verify_connectivity()
                        if query(root,'RETURN 1 AS ok')==[{'ok':1}]:
                            break
                    except (Neo4jError,ServiceUnavailable,OSError,ValueError):
                        pass
                    if time.monotonic()>deadline:
                        raise RuntimeError('NATIVE_GRAPH_START_TIMEOUT')
                    time.sleep(2)
                proc=query(root,"SHOW PROCEDURES YIELD name "
                    "WHERE name = 'b01research.claimFixture' RETURN name")
                if proc!=[{'name':'b01research.claimFixture'}]:
                    raise RuntimeError('RESEARCH_NATIVE_PROCEDURE_NOT_LOADED')
                query(root,'CREATE CONSTRAINT fixture_native_scope IF NOT EXISTS '
                    'FOR (g:FixtureFence) REQUIRE g.scope IS UNIQUE')
                query(root,'CREATE CONSTRAINT fixture_native_task IF NOT EXISTS '
                    'FOR (t:FixtureTask) REQUIRE t.id IS UNIQUE')
                statements=[
                    'CREATE ROLE '+role+' IF NOT EXISTS',
                    'GRANT ACCESS ON DATABASE neo4j TO '+role,
                    'GRANT MATCH {*} ON GRAPH neo4j NODES FixtureFence, FixtureTask TO '+role,
                    'GRANT EXECUTE PROCEDURE b01research.claimFixture ON DBMS TO '+role,
                    'GRANT EXECUTE BOOSTED PROCEDURE b01research.claimFixture ON DBMS TO '+role,
                    'CREATE USER '+user+' SET PASSWORD \''+password+
                    '\' CHANGE NOT REQUIRED',
                    'GRANT ROLE '+role+' TO '+user,
                ]
                for sql in statements:
                    query(root,sql,database='system')
                perms=query(root,'SHOW ROLE '+role+
                    ' PRIVILEGES AS COMMANDS YIELD command RETURN command',
                    database='system')
                if any(('GRANT SET ' in r['command']
                        or 'GRANT CREATE ' in r['command']
                        or 'GRANT DELETE ' in r['command']) for r in perms):
                    raise RuntimeError('UNSAFE_WRITER_ROLE_GRANTED')
                query(root,'CREATE (:FixtureFence {scope:\'fixture-assistx-b01\','
                    'instance:$instance,policy_sha256:$policy,term:1,'
                    'high_water:0,seen_nonces:[]})',
                    {'instance':instance,'policy':policy})
                for task in taskids:
                    query(root,'CREATE (:FixtureTask {id:$id,status:\'READY\','
                        'owner_id:$owner,recovery_agent_id:$owner,'
                        'requires_recovery_identity:true,'
                        'claimed_by:\'\',claim_nonce:\'\',claim_sequence:0})',
                        {'id':task,'owner':agent})
                def state():
                    guard=query(root,'MATCH (g:FixtureFence) '
                        'RETURN g.term AS term,g.high_water AS head,'
                        'g.seen_nonces AS nonces')[0]
                    tasks=query(root,'MATCH (t:FixtureTask) '
                        'RETURN t.id AS id,t.status AS status,'
                        't.claim_sequence AS sequence ORDER BY id')
                    return {'guard':guard,'tasks':tasks}
                with GraphDatabase.driver(uri,auth=(user,password),
                        connection_timeout=3,connection_acquisition_timeout=5) as actor:
                    before=state()
                    # Restricted principal cannot directly write via Cypher
                    # even though the boosted signed procedure can.
                    def direct_write():
                        return query(actor,'MATCH (t:FixtureTask {id:$id}) '
                            "SET t.status='CLAIMED' RETURN t.status AS status",
                            {'id':taskids[0]})
                    expect_rejected('direct_set_forbidden',direct_write,state,before)
                    expect_rejected('direct_create_forbidden',
                        lambda:query(actor,
                            'CREATE (:FixtureTask {id:$id}) RETURN 1 AS ok',
                            {'id':'fixture-task-'+tag+'-unauthorized'}),
                        state,before)
                    expect_rejected('direct_delete_forbidden',
                        lambda:query(actor,
                            'MATCH (t:FixtureTask {id:$id}) DELETE t',
                            {'id':taskids[0]}),state,before)
                    # Synthetic valid signer body assembled only after graph
                    # and role setup so short TTL remains valid.
                    def signed(task,seq,nonc,*,term=1,signer1=issuer,signer2=device,
                               signer_agent=agent,signed_policy=policy,
                               signed_instance=instance):
                        now=int(time.time())
                        return params(instance=signed_instance,task_id=task,
                            agent_id=signer_agent,term=term,sequence=seq,
                            nonce=nonc,policy_sha256=signed_policy,
                            issued_at=now-2,expires_at=now+54,
                            issuer=signer1,device=signer2)
                    one=signed(taskids[0],1,'nonce-'+tag+'-first')
                    for label,inputs in [
                        ('forged_issuer',signed(taskids[0],1,'nonce-'+tag+'-first',
                            signer1=other_issuer)),
                        ('forged_device',signed(taskids[0],1,'nonce-'+tag+'-first',
                            signer2=other_device)),
                        ('wrong_owner_signed',signed(taskids[0],1,'nonce-'+tag+'-first',
                            signer_agent='fixture-agent-foreign123')),
                        ('wrong_policy_signed',signed(taskids[0],1,'nonce-'+tag+'-first',
                            signed_policy='f'*64)),
                        ('wrong_instance_signed',signed(taskids[0],1,'nonce-'+tag+'-first',
                            signed_instance='e'*64)),
                    ]:
                        expect_rejected(label,lambda inputs=inputs:query(actor,CALL,inputs),
                                        state,before)
                    # The resource itself authenticates the double signature.
                    result=query(actor,CALL,one)
                    if result!=[{'result':'FIXTURE_CLAIMED','term':1,'highWater':1}]:
                        raise RuntimeError('SIGNED_NATIVE_RESOURCE_CLAIM_DENIED')
                    after=state()
                    if after['guard']['head']!=1 or after['tasks'][0]['status']!='CLAIMED':
                        raise RuntimeError('SIGNATURE_RESOURCE_EFFECT_NOT_APPLIED')
                    expect_rejected('valid_proof_replay',
                        lambda:query(actor,CALL,one),state,after)
                    expect_rejected('stale_sequence',
                        lambda:query(actor,CALL,signed(
                            taskids[1],1,'nonce-'+tag+'-stale')),state,after)
                    # Two valid signed owners attempt the same next head.
                    competing=[
                        signed(taskids[1],2,'nonce-'+tag+'-race-one'),
                        signed(taskids[2],2,'nonce-'+tag+'-race-two'),
                    ]
                    def contender(fields):
                        try:
                            return query(actor,CALL,fields)
                        except Neo4jError:
                            return None
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        outcome=list(pool.map(contender,competing))
                    if sum(x is not None for x in outcome)!=1:
                        raise RuntimeError('NATIVE_RESOURCE_DOUBLE_WINNER')
                    after_race=state()
                    if after_race['guard']['head']!=2 or sum(
                        t['status']=='CLAIMED' for t in after_race['tasks'])!=2:
                        raise RuntimeError('NATIVE_RESOURCE_RACE_STATE_BAD')
                    # The old term cannot claim a new Task following an
                    # admin-only research term advance.
                    query(root,'MATCH (g:FixtureFence) SET g.term=2')
                    after_term=state()
                    expect_rejected('stale_term_after_turnover',
                        lambda:query(actor,CALL,signed(taskids[3],3,
                            'nonce-'+tag+'-oldterm',term=1)),state,after_term)
                    term2=query(actor,CALL,signed(taskids[3],3,
                        'nonce-'+tag+'-newterm',term=2))
                    if term2!=[{'result':'FIXTURE_CLAIMED','term':2,'highWater':3}]:
                        raise RuntimeError('NEW_TERM_SIGNED_PROOF_NOT_ACCEPTED')
                    # Intentional graph-admin rollback negative. The same
                    # legitimately signed term1 claim can be accepted again
                    # once graph-local high-water and Task state are rewound.
                    query(root,'MATCH (g:FixtureFence) SET '
                        'g.term=1,g.high_water=0,g.seen_nonces=[]')
                    for task in taskids:
                        query(root,'MATCH (t:FixtureTask {id:$id}) '
                            "SET t.status='READY',t.claimed_by='',"
                            "t.claim_nonce='',t.claim_sequence=0",
                            {'id':task})
                    accepted_again=query(actor,CALL,one)
                    if accepted_again!=[{'result':'FIXTURE_CLAIMED',
                                        'term':1,'highWater':1}]:
                        raise RuntimeError('EXPECTED_GRAPH_RESTORE_REPLAY_NOT_SEEN')
                    # Restore replay was proven with the SAME pinned roots:
                    # only graph-local state changed; no re-enrollment.
                    return {
                        'schema':'assistx-b01-neo4j-native-ed25519-v0',
                        'verdict':'PASS_SYNTHETIC_WITH_GRAPH_RESTORE_NO_GO',
                        'ordinary_compromised_role_direct_cypher_denied':True,
                        'ordinary_compromised_role_no_set_property_grant':True,
                        'ordinary_compromised_role_create_and_delete_denied':True,
                        'term_turnover_rejected_old_signed_term':True,
                        'native_neo4j_ed25519_dual_signatures_verified':True,
                        'forged_and_foreign_signed_inputs_denied':True,
                        'resource_transaction_replay_denied_until_restore':True,
                        'competing_same_sequence_one_winner':True,
                        'valid_signed_proof_reaccepted_after_graph_rewind':True,
                        'public_keys_only_disposable_container_environment':True,
                        'cryptographic_extension_independently_reviewed':False,
                        'privileged_graph_admin_bypass_still_possible':True,
                        'independent_rollback_highwater':False,
                        'production_neo4j_plugin_installed':False,
                        'production_admission_authorized':False,
                        'production_graph_queried':False,
                        'host_published_ports':False,
                        'elapsed_seconds':round(time.monotonic()-started,2),
                    }
    finally:
        if created_graph:
            docker('rm','-f','-v',graph,timeout=27,check=False)
        if created_net:
            docker('network','rm',net,timeout=25,check=False)

if __name__=='__main__':
    print(json.dumps(run(),sort_keys=True))