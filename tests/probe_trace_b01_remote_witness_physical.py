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
    docker, assert_disposable, GRAPH_IMAGE, PYTHON_IMAGE)
from trace_b01_remote_monotonic_witness import bootstrap
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
def run(mode="normal"):
    if os.getenv('ASSISTX_B01_REMOTE_WITNESS_PHYSICAL')!='1':
        raise RuntimeError('EXPLICIT_DISPOSABLE_REMOTE_WITNESS_OPT_IN_REQUIRED')
    if mode not in ('normal','outage','co_restore'):
        raise RuntimeError('UNREVIEWED_DISPOSABLE_WITNESS_CASE')
    docker('image','inspect',GRAPH_IMAGE,timeout=12)
    tag=secrets.token_hex(5)
    graph='assistx-b01-rem-neo-'+tag
    net='assistx-b01-remgraph-'+tag
    witness_net='assistx-b01-remwitness-'+tag
    witness='assistx-b01-rem-witness-'+tag
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
    created_witness=False
    created_witness_net=False
    witness_key=Ed25519PrivateKey.generate()
    witness_bearer=secrets.token_urlsafe(35)
    started=time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='assistx-b01-native-lab-') as temp:
            jar=make_jar(Path(temp))
            rootdir=Path(temp)
            (rootdir/'witness-db').mkdir()
            (rootdir/'witness-anchor').mkdir()
            bootstrap(rootdir/'witness-db'/'ledger.sqlite',
                rootdir/'witness-anchor'/'checkpoint.json',
                instance=instance,agent_id=agent,policy_sha256=policy,
                issuer_root=public(issuer),device_root=public(device),
                witness_root=public(witness_key))
            witness_cfg=dict(db='/tmp/witness-db/ledger.sqlite',
                anchor='/tmp/witness-anchor/checkpoint.json',
                bearer=witness_bearer,issuer_root=public(issuer).hex(),
                device_root=public(device).hex(),
                witness_seed=witness_key.private_bytes_raw().hex())
            (rootdir/'witness-config.json').write_text(json.dumps(witness_cfg))
            (rootdir/'witness-config.json').chmod(0o600)
            docker('image','inspect',PYTHON_IMAGE,timeout=12)
            docker('network','create','--internal','--driver','bridge',
                '--label','assistx.b01.research=remote-witness',witness_net)
            created_witness_net=True
            docker('create','--pull','never','--name',witness,
                '--network',witness_net,'--network-alias','b01-witness',
                '--memory','160m','--cpus','0.6','--pids-limit','64',
                PYTHON_IMAGE,'python','-u','/tmp/witness.py',
                '/tmp/witness-config.json',timeout=25)
            created_witness=True
            assert_disposable(witness,{witness_net})
            for src,dest in [
                (rootdir/'witness-config.json','witness-config.json'),
                (rootdir/'witness-db','witness-db'),
                (rootdir/'witness-anchor','witness-anchor'),
                (SOURCE.parent.parent/'trace_b01_remote_monotonic_witness.py','witness.py'),
                (SOURCE.parent.parent/'trace_b01_native_claim_protocol.py',
                 'trace_b01_native_claim_protocol.py'),
                (SOURCE.parent.parent/'trace_b01_signed_reservation_gate.py',
                 'trace_b01_signed_reservation_gate.py'),
                (Path('/usr/lib/python3/dist-packages/cryptography'),'cryptography'),
                (Path('/usr/lib/python3/dist-packages/'
                      '_cffi_backend.cpython-312-x86_64-linux-gnu.so'),
                 '_cffi_backend.cpython-312-x86_64-linux-gnu.so'),
            ]:
                if not src.exists():
                    raise RuntimeError('WITNESS_CACHED_DEPENDENCY_UNAVAILABLE')
                docker('cp',str(src),witness+':/tmp/'+dest)
            docker('network','create','--internal','--driver','bridge',
                '--label','assistx.b01.research=remote-witness',net)
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
                '--env','B01_FIXTURE_WITNESS_ROOT='+public(witness_key).hex(),
                '--env','B01_FIXTURE_WITNESS_URL=http://b01-witness:8812/reserve',
                '--env','B01_FIXTURE_WITNESS_BEARER='+witness_bearer,
                GRAPH_IMAGE,timeout=27)
            created_graph=True
            docker('network','connect',witness_net,graph)
            docker('cp',str(jar),graph+':/var/lib/neo4j/plugins/b01-fixture.jar')
            docker('start',graph,timeout=27)
            info=assert_disposable(graph,{net,witness_net})
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
                    # Start the witness before any Java URL resolution;
                    # Docker DNS for stopped containers can cache a negative
                    # answer in Neo4j's JVM beyond the outage itself.
                    docker('start',witness,timeout=15)
                    deadline=time.monotonic()+22
                    while True:
                        health=docker('exec',witness,'python','-c',
                            "import urllib.request;"
                            "assert urllib.request.urlopen("
                            "'http://127.0.0.1:8812/health',timeout=2).status==200",
                            timeout=6,check=False)
                        if health.returncode==0:
                            break
                        if time.monotonic()>=deadline:
                            raise RuntimeError('REMOTE_WITNESS_NOT_READY')
                        time.sleep(0.5)
                    if mode=='outage':
                        # Running witness keeps Docker DNS stable; pause it
                        # while the native procedure is awaiting a receipt.
                        docker('pause',witness,timeout=10)
                        try:
                            expect_rejected('paused_uncertain',
                                lambda:query(actor,CALL,one),state,before)
                        finally:
                            docker('unpause',witness,timeout=10)
                        time.sleep(0.75)
                        check=docker('exec',witness,'python','-c',
                            'import sqlite3,json;'
                            'c=sqlite3.connect("/tmp/witness-db/ledger.sqlite");'
                            's=json.loads(c.execute("SELECT document FROM state WHERE id=1")'
                            '.fetchone()[0]);'
                            'print(s["high_water"],int(s["pending"] is not None))',
                            timeout=8)
                        values=check.stdout.strip().split()
                        if len(values)!=2 or values[0] not in ('0','1'):
                            raise RuntimeError('UNEXPECTED_WITNESS_UNCERTAINTY_STATE')
                        return {
                            'schema':'assistx-b01-remote-uncertain-outage-v0',
                            'verdict':'PASS_FAIL_CLOSED_UNCERTAIN_NO_AUTO_RETRY',
                            'graph_state_unchanged_after_witness_timeout':state()==before,
                            'late_witness_pending_reservation_observed':values==['1','1'],
                            'automatic_reexecution_authorized':False,
                            'remote_graph_and_witness_atomicity_proven':False,
                            'production_graph_queried':False,
                            'elapsed_seconds':round(time.monotonic()-started,2),
                        }
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
                    # A simple disposable restart preserves SQLite+anchor;
                    # this is NOT protected across removal/reprovisioning.
                    docker('restart',witness,timeout=16)
                    check=docker('exec',witness,'python','-c',
                        'import sqlite3,json;'
                        'c=sqlite3.connect("/tmp/witness-db/ledger.sqlite");'
                        's=json.loads(c.execute("SELECT document FROM state WHERE id=1")'
                        '.fetchone()[0]);'
                        'assert s["high_water"]==1 and s["pending"] is not None',
                        timeout=8)
                    if check.returncode!=0:
                        raise RuntimeError('REMOTE_WITNESS_PENDING_LOST_ON_RESTART')
                    # Graph-only admin rewind deliberately reopens the same
                    # local term/sequence/nonce. The independent witness must
                    # still DENY the old valid signed bundle.
                    query(root,'MATCH (g:FixtureFence) SET '
                        'g.term=1,g.high_water=0,g.seen_nonces=[]')
                    query(root,'MATCH (t:FixtureTask {id:$id}) '
                        "SET t.status='READY',t.claimed_by='',"
                        "t.claim_nonce='',t.claim_sequence=0",
                        {'id':taskids[0]})
                    rewound=state()
                    if mode=='co_restore':
                        # WARNING: deliberate ONLY-DISPOSABLE controlled
                        # rollback of the separate witness's database AND
                        # checkpoint, using copies made before the claim.
                        docker('stop',witness,timeout=15)
                        docker('cp',str(rootdir/'witness-db'/'ledger.sqlite'),
                            witness+':/tmp/witness-db/ledger.sqlite')
                        docker('cp',str(rootdir/'witness-anchor'/'checkpoint.json'),
                            witness+':/tmp/witness-anchor/checkpoint.json')
                        docker('start',witness,timeout=15)
                        deadline=time.monotonic()+20
                        while True:
                            health=docker('exec',witness,'python','-c',
                                'import urllib.request;'
                                "assert urllib.request.urlopen("
                                "'http://127.0.0.1:8812/health',timeout=2).status==200",
                                timeout=6,check=False)
                            if health.returncode==0:
                                break
                            if time.monotonic()>=deadline:
                                raise RuntimeError('RESTORED_WITNESS_NOT_READY')
                            time.sleep(0.4)
                        admitted=query(actor,CALL,one)
                        if admitted!=[{'result':'FIXTURE_CLAIMED','term':1,'highWater':1}]:
                            raise RuntimeError('EXPECTED_COORDINATED_REPLAY_NOT_REPRODUCED')
                        return {
                            'schema':'assistx-b01-coordinated-restore-negative-v0',
                            'verdict':'PASS_EXPECTED_COHERENT_ROLLBACK_REPLAY_NO_GO',
                            'previously_consumed_dual_signed_proof_readmitted':True,
                            'graph_and_witness_rewound_together':True,
                            'same_test_roots_and_instance':True,
                            'independent_immutable_anchor_proven':False,
                            'production_admission_authorized':False,
                            'production_graph_queried':False,
                            'elapsed_seconds':round(time.monotonic()-started,2),
                        }
                    expect_rejected('graph_only_restore_signed_replay',
                        lambda:query(actor,CALL,one),state,rewound)
                    return {
                        'schema':'assistx-b01-remote-native-witness-v0',
                        'verdict':'PASS_GRAPH_ONLY_REPLAY_DENIED_PENDING_NO_GO',
                        'graph_native_dual_ed25519_claim_applied':True,
                        'remote_witness_independently_verified_dual_signatures':True,
                        'restricted_actor_direct_graph_write_denied':True,
                        'fresh_challenge_receipt_verified_inside_graph_proc':True,
                        'remote_pending_survived_disposable_container_restart':True,
                        'old_valid_signature_denied_after_graph_rewind':True,
                        'atomic_graph_and_witness_commit':False,
                        'automatic_reconciliation_or_failover_safe':False,
                        'coordinated_witness_restore_resistant':False,
                        'privileged_admin_graph_bypass_resistant':False,
                        'production_admission_authorized':False,
                        'production_graph_queried':False,
                        'host_published_ports':False,
                        'elapsed_seconds':round(time.monotonic()-started,2),
                    }
    finally:
        if created_graph:
            docker('rm','-f','-v',graph,timeout=27,check=False)
        if created_witness:
            docker('rm','-f','-v',witness,timeout=27,check=False)
        if created_net:
            docker('network','rm',net,timeout=25,check=False)
        if created_witness_net:
            docker('network','rm',witness_net,timeout=25,check=False)

if __name__=='__main__':
    print(json.dumps(run(os.getenv('ASSISTX_B01_REMOTE_WITNESS_CASE','normal')),sort_keys=True))