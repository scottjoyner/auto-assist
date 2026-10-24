#!/usr/bin/env python3
"""CI-only real rootless broker + private credential pipe + Neo4j transactions.

The synthetic root provisioner reads its temporary DB admin password on
stdin (never command-line arguments); it passes a separate generated restricted
writer password to the nonroot broker through one inherited anonymous pipe.
No production service/credential/Task/socket is accessed. Root is a CI
provisioner, NOT the broker. Root/admin can inherently inspect all processes:
this is not independent key custody or a production authorization result.
"""
from __future__ import annotations

if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("explicit ephemeral Enterprise credential-isolation canary only")

import importlib.util
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import types
import uuid

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError,ServiceUnavailable

assert os.environ.get("HERMES_ROOTLESS_DB_CI") == "synthetic-only"
assert os.environ.get("GITHUB_ACTIONS") == "true"
assert "NEO4J_URI" not in os.environ
assert "NEO4J_PASSWORD" not in os.environ
assert "NEO4J_AUTH" not in os.environ
URI="bolt://127.0.0.1:7687"
BROKER_USER="synthetic_rootless_broker"
READER_USER="synthetic_rootless_reader"
BROKER_ROLE="synthetic_rootless_broker_role"
READER_ROLE="synthetic_rootless_reader_role"
OWNER="hermes-local"
TASK_ID="SYNTHETIC_ROOTLESS_DB_"+uuid.uuid4().hex[:15]
MODULES=(
    "hermes_claim_census","hermes_source_generation_research",
    "hermes_source_heartbeat_research","hermes_source_completion_research",
    "hermes_source_receipt_research",
    "hermes_broker_command_research","hermes_broker_unix_boundary_research",
    "hermes_broker_rootless_research",
)
src=Path(__file__).resolve().parents[1]/"src/assistx"


def load_candidate():
    pkg=types.ModuleType("assistx")
    pkg.__path__=[str(src)]
    sys.modules["assistx"]=pkg
    for name in MODULES:
        spec=importlib.util.spec_from_file_location("assistx."+name,src/(name+".py"))
        module=importlib.util.module_from_spec(spec)
        sys.modules["assistx."+name]=module
        spec.loader.exec_module(module)
    from assistx import hermes_broker_rootless_research as listener
    return listener


def rootless_child(directory: str,allowed_uid: int,client_gid: int,
                   secret_fd: int):
    assert os.geteuid()!=0 and os.getegid()!=0
    assert os.geteuid()!=allowed_uid
    assert "NEO4J_PASSWORD" not in os.environ
    assert "HERMES_RBAC_EPHEMERAL_ADMIN_PASSWORD" not in os.environ
    raw=os.read(secret_fd,256)
    os.close(secret_fd)
    assert raw.endswith(b"\n") and len(raw)<=256 and len(raw)>=24
    password=raw[:-1].decode("ascii")
    from neo4j import GraphDatabase as Driver
    driver=Driver.driver(URI,auth=(BROKER_USER,password),
                         connection_timeout=5,max_transaction_retry_time=8)
    try:
        driver.verify_connectivity()
        listener=load_candidate()
        def session_factory():
            return driver.session(database="neo4j")
        result=listener.serve_rootless_candidate(
            socket_dir=Path(directory),allowed_peer_uid=allowed_uid,
            client_gid=client_gid,session_factory=session_factory,
            max_connections=8,max_lifetime_seconds=27.0,
            emit_holding_replies=True)
        assert result["accepted_candidate_commands"]==6,result
        assert result["rejected_connections"]==2,result
        assert result["broker_exclusive_credentials_verified"] is False
        assert result["production_dispatch_authorized"] is False
        assert result["decision"]=="HOLD"
        assert result["socket_replies_durable"] is False
        assert result["unconfirmed_socket_replies"]==0
        print("PASS rootless_private_credential_two_transactions_two_denials",flush=True)
        print("PASS rootless_reply_delivery_with_source_HOLD_not_durable",flush=True)
    finally:
        driver.close()


def drop_to(uid: int,gid: int):
    def inner():
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    return inner


def send_unix(path: Path,cmd: dict,uid: int,gid: int) -> dict | None:
    packet=json.dumps({"version":1,"command":cmd},
                      separators=(",",":")).encode("utf8")+b"\n"
    code="""import socket,sys
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
s.settimeout(5)
s.connect(sys.argv[1])
s.sendall(bytes.fromhex(sys.argv[2]))
s.shutdown(socket.SHUT_WR)
try:
    packet=s.recv(512)
except (ConnectionResetError,BrokenPipeError):
    packet=b""
sys.stdout.buffer.write(packet)
s.close()
"""
    out=subprocess.run(
        [sys.executable,"-c",code,str(path),packet.hex()],
        preexec_fn=drop_to(uid,gid),
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        timeout=8,check=False)
    assert out.returncode==0,"synthetic_client_socket_send_failed"
    if not out.stdout:
        return None
    assert len(out.stdout)<=192 and out.stdout.endswith(b"\n")
    reply=json.loads(out.stdout.decode("ascii"))
    assert set(reply).issubset({
        "schema","accepted_source_transaction","candidate_generation",
        "reconciled","error","durable_delivery_verified","production_dispatch_authorized",
        "decision",
    })
    assert reply["decision"]=="HOLD"
    assert reply["durable_delivery_verified"] is False
    assert reply["production_dispatch_authorized"] is False
    assert "password" not in str(reply).lower()
    assert "task_id" not in str(reply).lower()
    return reply


def probe_cross_uid_memory(pid: int,uid: int,gid: int) -> None:
    """Unprivileged CI client cannot read broker RAM or environment."""
    code="""import os,sys
pid=int(sys.argv[1])
for suffix in ("mem","environ"):
    try:
        fd=os.open(f"/proc/{pid}/{suffix}",os.O_RDONLY)
    except PermissionError:
        continue
    else:
        os.close(fd)
        raise SystemExit("broker_proc_visibility_violation")
print("CROSS_UID_PROC_DENIED")
"""
    out=subprocess.run(
        [sys.executable,"-c",code,str(pid)],
        preexec_fn=drop_to(uid,gid),
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        timeout=5,check=False)
    assert out.returncode==0 and out.stdout.strip()==b"CROSS_UID_PROC_DENIED",(
        "unprivileged_broker_memory_or_environment_readable")
    print("PASS unprivileged_socket_client_cannot_read_broker_proc_memory_env",
          flush=True)


def main():
    assert os.geteuid()==0, "ephemeral CI provisioner requires root"
    # No admin credential in argv/env, and it is never handed to the broker.
    admin_pass=sys.stdin.readline(256).rstrip("\n")
    assert len(admin_pass)>=20
    writer_pass=secrets.token_urlsafe(32)
    reader_pass=secrets.token_urlsafe(32)
    broker_uid=pwd.getpwnam("daemon")
    client_uid=pwd.getpwnam("nobody")
    assert broker_uid.pw_uid>0 and client_uid.pw_uid>0
    assert broker_uid.pw_uid!=client_uid.pw_uid
    assert broker_uid.pw_gid>0 and client_uid.pw_gid>0
    admin=GraphDatabase.driver(URI,auth=("neo4j",admin_pass),connection_timeout=5)
    for n in range(75):
        try:admin.verify_connectivity();break
        except (Neo4jError,ServiceUnavailable,OSError):
            if n==74:raise
            time.sleep(1)
    def sysquery(q,**kwargs):
        with admin.session(database="system") as s:
            return s.run(q,kwargs).consume()
    def query(q,**kwargs):
        with admin.session(database="neo4j") as s:
            return s.run(q,kwargs).single()
    broker_proc=None
    try:
        query("""CREATE CONSTRAINT hermes_source_generation_owner_unique IF NOT EXISTS
                 FOR(g:HermesSourceGeneration) REQUIRE g.owner IS UNIQUE""")
        query("""CREATE CONSTRAINT hermes_source_receipt_key_unique IF NOT EXISTS
                 FOR(r:HermesSourceReceipt) REQUIRE r.key IS UNIQUE""")
        # Create all receipt property-name tokens up-front as admin; the
        # restricted broker must not CREATE arbitrary new property names.
        query("""CREATE(r:HermesSourceReceipt {
            key:'synthetic-seed-only',owner:'hermes-local',
            operation:'heartbeat',request_digest:'synthetic',
            source_generation:0,created_at_ts:timestamp()})
            RETURN r.key AS key""")
        query("MATCH(r:HermesSourceReceipt{key:'synthetic-seed-only'}) DELETE r")
        query("CREATE(g:HermesSourceGeneration {owner:'hermes-local',generation:0,lock_epoch:0})")
        for tid in (TASK_ID,TASK_ID+"_receipt"):
            query("""
                CREATE(t:Task{id:$id,status:'RUNNING',claimed_by:'hermes-local',
                    target_agent_id:'hermes-local',claim_id:'synthetic-claim',
                    agent_session_id:'synthetic-session',execution_attempt:1,
                    lease_expires_at_ts:timestamp()+120000,
                    hermes_fence_check_epoch:0,heartbeat_by:'hermes-local',
                    heartbeat_at_ts:0,paused_from_agent_id:null,
                    failed_by:null,migration_target_agent_id:null})
                RETURN t.id AS id
            """,id=tid)
        # Seed property names without marking current claim completed.
        query("""CREATE(t:Task{id:$id,status:'READY',completed_by:'synthetic',
                 completed_at:datetime(),completed_at_ts:0})
                 RETURN t.id AS id""",id=TASK_ID+"_tokens")
        for name,password in ((BROKER_USER,writer_pass),(READER_USER,reader_pass)):
            sysquery(f"CREATE USER {name} IF NOT EXISTS "
                     "SET PASSWORD $pw CHANGE NOT REQUIRED",pw=password)
        sysquery(f"CREATE ROLE {READER_ROLE} IF NOT EXISTS AS COPY OF reader")
        sysquery(f"GRANT ROLE {READER_ROLE} TO {READER_USER}")
        sysquery(f"CREATE ROLE {BROKER_ROLE} IF NOT EXISTS")
        sysquery(f"GRANT ACCESS ON DATABASE neo4j TO {BROKER_ROLE}")
        sysquery(f"GRANT MATCH {{*}} ON GRAPH neo4j NODES * TO {BROKER_ROLE}")
        sysquery(f"GRANT SHOW CONSTRAINT ON DATABASE neo4j TO {BROKER_ROLE}")
        sysquery(f"GRANT SET PROPERTY "
                 "{hermes_fence_check_epoch,status,completed_by,completed_at,"
                 "completed_at_ts,heartbeat_by,heartbeat_at_ts,lease_expires_at_ts} "
                 f"ON GRAPH neo4j NODES Task TO {BROKER_ROLE}")
        sysquery(f"GRANT SET PROPERTY {{lock_epoch,generation}} "
                 f"ON GRAPH neo4j NODES HermesSourceGeneration TO {BROKER_ROLE}")
        sysquery(f"GRANT CREATE ON GRAPH neo4j NODES HermesSourceReceipt "
                 f"TO {BROKER_ROLE}")
        sysquery(f"GRANT SET PROPERTY "
                 "{key,owner,operation,request_digest,source_generation,created_at_ts} "
                 f"ON GRAPH neo4j NODES HermesSourceReceipt TO {BROKER_ROLE}")
        sysquery(f"GRANT ROLE {BROKER_ROLE} TO {BROKER_USER}")
        with GraphDatabase.driver(URI,auth=(READER_USER,reader_pass)) as reader:
            with reader.session(database="neo4j") as s:
                assert s.run("MATCH(t:Task{id:$id}) RETURN t.status AS status",
                             id=TASK_ID).single()["status"]=="RUNNING"
                try:
                    s.run("MATCH(t:Task{id:$id}) SET t.status='DONE'",id=TASK_ID).consume()
                except Neo4jError as ex:
                    assert ex.code and ex.code.startswith("Neo.ClientError.Security.")
                else:
                    raise AssertionError("legacy reader direct Task write allowed")
        print("PASS nonbroker_reader_direct_Bolt_Task_write_denied",flush=True)

        with tempfile.TemporaryDirectory(prefix="rootless-db-ci-") as td:
            root=Path(td)
            os.chmod(root,0o755)
            private=root/"private"
            private.mkdir(mode=0o700)
            os.chown(private,broker_uid.pw_uid,client_uid.pw_gid)
            os.chmod(private,0o2710)
            stage=root/"stage"
            (stage/"tests").mkdir(parents=True,mode=0o755)
            staged_src=stage/"src"/"assistx"
            staged_src.mkdir(parents=True,mode=0o755)
            for folder in (stage,stage/"src",stage/"tests",staged_src):
                os.chmod(folder,0o755)
            staged_script=stage/"tests"/Path(__file__).name
            shutil.copyfile(Path(__file__).resolve(),staged_script)
            os.chmod(staged_script,0o644)
            for name in MODULES:
                dst=staged_src/(name+".py")
                shutil.copyfile(src/(name+".py"),dst)
                os.chmod(dst,0o644)

            read_fd,write_fd=os.pipe()
            try:
                args=[sys.executable,str(staged_script),"--child",
                      str(private),str(client_uid.pw_uid),
                      str(client_uid.pw_gid),str(read_fd)]
                # Explicit minimal environment: no admin/client/writer credential
                # and no database URI or other provider secrets.
                env={"PATH":os.environ.get("PATH","/usr/bin:/bin"),
                     "HOME":str(root),"GITHUB_ACTIONS":"true",
                     "HERMES_ROOTLESS_DB_CI":"synthetic-only"}
                broker_proc=subprocess.Popen(
                    args,env=env,pass_fds=(read_fd,),
                    preexec_fn=drop_to(broker_uid.pw_uid,broker_uid.pw_gid),
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            finally:
                os.close(read_fd)
            try:
                os.write(write_fd,writer_pass.encode("ascii")+b"\n")
            finally:
                os.close(write_fd)
            socket_path=private/"broker.sock"
            deadline=time.monotonic()+10
            while not socket_path.exists() and broker_proc.poll() is None and time.monotonic()<deadline:
                time.sleep(0.025)
            if broker_proc.poll() is not None:
                _,error=broker_proc.communicate(timeout=2)
                last=error.decode(errors="replace").splitlines()
                raise AssertionError("rootless_credential_broker_start_failed:"+
                                     (last[-1][:150] if last else "unknown"))
            assert socket_path.exists(),"rootless_credential_socket_unavailable"
            meta=socket_path.stat()
            assert stat.S_ISSOCK(meta.st_mode)
            assert stat.S_IMODE(meta.st_mode)==0o660
            assert meta.st_uid==broker_uid.pw_uid and meta.st_gid==client_uid.pw_gid
            environ=Path(f"/proc/{broker_proc.pid}/environ").read_bytes()
            cmdline=Path(f"/proc/{broker_proc.pid}/cmdline").read_bytes()
            for secret in (writer_pass,reader_pass,admin_pass):
                assert secret.encode() not in environ
                assert secret.encode() not in cmdline
            print("PASS restricted_broker_writer_password_pipe_not_argv_or_env",flush=True)
            probe_cross_uid_memory(broker_proc.pid,client_uid.pw_uid,client_uid.pw_gid)

            base={"task_id":TASK_ID,"claim_id":"synthetic-claim",
                  "session_id":"synthetic-session","attempt":1}
            hb={"operation":"heartbeat","expected_generation":0,**base,"ttl_ms":30000}
            completion={"operation":"complete","expected_generation":1,**base}
            # Caller with broker's UID is not the allowed separate client.
            unauthorized=send_unix(
                socket_path,hb,broker_uid.pw_uid,broker_uid.pw_gid)
            assert unauthorized is None, "same-UID peer saw a reply"
            first=send_unix(socket_path,hb,client_uid.pw_uid,client_uid.pw_gid)
            assert first is not None
            assert first["accepted_source_transaction"] is True
            assert first["candidate_generation"]==1
            def get_state():
                r=query("""MATCH(t:Task{id:$id}),
                                  (g:HermesSourceGeneration{owner:'hermes-local'})
                             RETURN t.status AS status,g.generation AS generation""",
                        id=TASK_ID)
                return (r["status"],r["generation"])
            deadline=time.monotonic()+8
            while get_state()!=("RUNNING",1) and time.monotonic()<deadline:
                time.sleep(0.03)
            assert get_state()==("RUNNING",1),"rootless_heartbeat_not_committed"
            stale=send_unix(socket_path,hb,client_uid.pw_uid,client_uid.pw_gid)
            assert stale is not None
            assert stale["accepted_source_transaction"] is False
            assert stale["error"]=="request_denied"
            assert "candidate_generation" not in stale
            second=send_unix(socket_path,completion,client_uid.pw_uid,client_uid.pw_gid)
            assert second is not None
            assert second["accepted_source_transaction"] is True
            assert second["candidate_generation"]==2
            print("PASS socket_receipts_qualified_HOLD_without_secrets",flush=True)

            # Recorded operations write a minimal receipt in the SAME source
            # transaction. Reconcile is read-only through the separate UID
            # and must not bump generation or repeat a Task mutation.
            receipt_base={**base,"task_id":TASK_ID+"_receipt"}
            receipt_key_hb=str(uuid.uuid4())
            receipt_key_complete=str(uuid.uuid4())
            recorded_hb={
                "operation":"heartbeat_recorded","expected_generation":2,
                "receipt_key":receipt_key_hb,**receipt_base,"ttl_ms":30000,
            }
            reconcile_hb={**recorded_hb,"operation":"reconcile_heartbeat"}
            recorded_complete={
                "operation":"complete_recorded","expected_generation":3,
                "receipt_key":receipt_key_complete,**receipt_base,
            }
            reconcile_complete={**recorded_complete,"operation":"reconcile_complete"}
            recorded=send_unix(
                socket_path,recorded_hb,client_uid.pw_uid,client_uid.pw_gid)
            assert recorded is not None
            assert recorded["accepted_source_transaction"] is True
            assert recorded["candidate_generation"]==3
            assert "reconciled" not in recorded
            prior=send_unix(
                socket_path,reconcile_hb,client_uid.pw_uid,client_uid.pw_gid)
            assert prior is not None
            assert prior["reconciled"] is True
            assert prior["candidate_generation"]==3
            assert prior["durable_delivery_verified"] is False
            complete_again=send_unix(
                socket_path,recorded_complete,client_uid.pw_uid,client_uid.pw_gid)
            assert complete_again is not None
            assert complete_again["candidate_generation"]==4
            assert complete_again["accepted_source_transaction"] is True
            assert "reconciled" not in complete_again
            prior_done=send_unix(
                socket_path,reconcile_complete,client_uid.pw_uid,client_uid.pw_gid)
            assert prior_done is not None
            assert prior_done["reconciled"] is True
            assert prior_done["candidate_generation"]==4

            receipt_state=query("""
                MATCH(t:Task{id:$id}),
                     (g:HermesSourceGeneration{owner:'hermes-local'})
                RETURN t.status AS status,g.generation AS generation
            """,id=TASK_ID+"_receipt")
            assert receipt_state["status"]=="DONE"
            assert receipt_state["generation"]==4
            for key,expect in ((receipt_key_hb,3),(receipt_key_complete,4)):
                row=query("""MATCH(r:HermesSourceReceipt{key:$key})
                    RETURN r.source_generation AS generation,
                           r.request_digest AS digest, r.operation AS operation
                """,key=key)
                assert row is not None
                assert row["generation"]==expect
                assert len(row["digest"])==64
            print("PASS rootless_socket_source_receipt_reconciles_without_rewriting",flush=True)
            stdout,error=broker_proc.communicate(timeout=14)
            assert broker_proc.returncode==0,"rootless_broker_transaction_failed"
            assert b"PASS rootless_private_credential_two_transactions_two_denials" in stdout
            assert get_state()==("DONE",2),"rootless_completion_fence_failed"
            assert not socket_path.exists(),"rootless_socket_not_cleaned"
            print("PASS rootless_nonroot_private_credential_enrolled_heartbeat_completion",flush=True)
            print("PASS stale_generation_rejected_without_ledger_increment",flush=True)
        print("HERMES_ROOTLESS_PRIVATE_DB_PIPE_CI_PASS=8",flush=True)
        print("PRODUCTION_TASKS_TOUCHED=0;PRODUCTION_SERVICES_TOUCHED=0;DECISION=HOLD",
              flush=True)
    finally:
        if broker_proc is not None and broker_proc.poll() is None:
            broker_proc.kill()
            broker_proc.communicate(timeout=5)
        try:
            query("MATCH(t:Task) WHERE t.id STARTS WITH $prefix DETACH DELETE t",
                  prefix=TASK_ID)
            query("MATCH(g:HermesSourceGeneration{owner:'hermes-local'}) DETACH DELETE g")
            query("MATCH(r:HermesSourceReceipt) DETACH DELETE r")
            query("DROP CONSTRAINT hermes_source_receipt_key_unique IF EXISTS")
            query("DROP CONSTRAINT hermes_source_generation_owner_unique IF EXISTS")
            for name in (BROKER_USER,READER_USER):
                sysquery(f"DROP USER {name} IF EXISTS")
            for role in (BROKER_ROLE,READER_ROLE):
                sysquery(f"DROP ROLE {role} IF EXISTS")
        finally:
            admin.close()


if len(sys.argv)==6 and sys.argv[1]=="--child":
    rootless_child(sys.argv[2],int(sys.argv[3]),int(sys.argv[4]),int(sys.argv[5]))
elif len(sys.argv)==1:
    main()
else:
    raise SystemExit("invalid synthetic CI arguments")
