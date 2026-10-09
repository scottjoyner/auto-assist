"""Isolated SCRAM PostgreSQL role-fenced admission research — never production.

The Docker fixture has NO published ports, bind mounts, other networks, or
persistent data. Bootstrap secrets and signing keys remain out of the repo.
Workers can invoke admit/inspect but cannot read/write/delete tables, install
schema, impersonate verifier, or invoke the privileged release function.
Still single-primary, not quorum HA, and NOT physically bound to Neo4j.
"""
from __future__ import annotations
import json
import multiprocessing as mp
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

import psycopg
from psycopg import sql

CONTAINER = "assistx-trace-privilege-pg-20261009"
NETWORK = "assistx-trace-privilege-net-20261009"
WORKER = "assistx_trace_research_worker"
VERIFIER = "assistx_trace_research_verifier"
EPOCH_FORMAT = re.compile(r"^[0-9a-f-]{36}$")
SCHEMA = "assistx_trace_fence_research"
ROOT = Path(__file__).resolve().parents[1]


def _inspect(*command):
    result = subprocess.run(["docker",*command],check=True,capture_output=True,
                            text=True,timeout=8)
    return json.loads(result.stdout)


def _address():
    if os.getenv("ASSISTX_TRACE_PG_PRIVILEGE_EXPERIMENT") != "1":
        raise RuntimeError("EXPLICIT_OPT_IN_REQUIRED")
    objs = _inspect("inspect",CONTAINER)
    nets = _inspect("network","inspect",NETWORK)
    if len(objs)!=1 or len(nets)!=1:
        raise RuntimeError("MISSING_FIXTURE")
    obj,n=objs[0],nets[0]
    h=obj["HostConfig"]
    ip=obj["NetworkSettings"]["Networks"][NETWORK]["IPAddress"]
    if (obj["Name"]!="/"+CONTAINER
        or not obj["State"]["Running"]
        or obj["Config"]["Image"]!="postgres:17-alpine"
        or h["NetworkMode"]!=NETWORK
        or set(obj["NetworkSettings"]["Networks"])!={NETWORK}
        or not n["Internal"] or h.get("PortBindings")
        or any(m.get("Type")=="bind" for m in obj["Mounts"])
        or not 0 < h["Memory"]<=536_870_912 or h["NanoCpus"]>1_000_000_000
        or "POSTGRES_HOST_AUTH_METHOD=scram-sha-256" not in obj["Config"]["Env"]
        or not ip.startswith("172.") or not ip.endswith(".2")):
        raise RuntimeError("UNSAFE_FIXTURE")
    return ip


def _dsn(role,password,ip):
    # Avoid manually constructing URLs from secret bytes or logging secrets.
    return psycopg.conninfo.make_conninfo(
        host=ip,port=5432,dbname="postgres",user=role,
        password=password,connect_timeout=3,
        options="-c statement_timeout=4000",
    )


def _open(dsn):
    return psycopg.connect(dsn,connect_timeout=3)


def bootstrap_once(admin_dsn,worker_password,verifier_password,epoch):
    if (not isinstance(epoch,str) or not EPOCH_FORMAT.fullmatch(epoch)
        or not worker_password or not verifier_password):
        raise ValueError("RESEARCH_GENESIS_REQUIRED")
    path=ROOT/"research"/"trace_pg_privilege_fence.sql"
    ddl=path.read_text()
    with _open(admin_dsn) as db:
        # Refuse to rearm after an authority downgrade or partial failure.
        exists=db.execute("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s)",
                          (SCHEMA,)).fetchone()[0]
        if exists:
            raise RuntimeError("AUTHORITY_ALREADY_EXISTS")
        # Explicit one-time test/bootstrap, never an API startup path.
        db.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(WORKER),sql.Literal(worker_password)))
        db.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(VERIFIER),sql.Literal(verifier_password)))
        db.execute(ddl)
        db.execute("INSERT INTO assistx_trace_fence_research.authority "
                   "VALUES (true,%s,3,'assistx-pg-privilege-v1')",(epoch,))
        for role in (WORKER,VERIFIER):
            db.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(SCHEMA),sql.Identifier(role)))
        db.execute(sql.SQL("GRANT EXECUTE ON FUNCTION {}.admit(text,text,text), "
                           "{}.inspect(text) TO {}").format(
            sql.Identifier(SCHEMA),sql.Identifier(SCHEMA),
            sql.Identifier(WORKER)))
        db.execute(sql.SQL("GRANT EXECUTE ON FUNCTION {}.inspect(text), "
                           "{}.release_exact(text,text,text) TO {}").format(
            sql.Identifier(SCHEMA),sql.Identifier(SCHEMA),
            sql.Identifier(VERIFIER)))


def _worker(ip,password,epoch,ref,start,physical,results):
    start.wait(12)
    try:
        with _open(_dsn(WORKER,password,ip)) as db:
            result=db.execute(
                "SELECT * FROM assistx_trace_fence_research.admit(%s,%s,%s)",
                (epoch,uuid.uuid4().hex,ref)).fetchone()
            # Admission must commit BEFORE the worker starts its physical
            # read. Holding the metadata row lock across a long graph query
            # would serially block unrelated worker admission.
            db.commit()
            results.put({"ref":ref,"accepted":result[0],"occupancy":result[1],
                         "reason":result[2]})
            if not result[0]:
                return
            physical.wait(12)
            db.execute("SET application_name='assistx_pg_privileged_read_probe'")
            db.execute("SELECT pg_sleep(2.6)")
    except Exception as exc:
        results.put({"ref":ref,"failure":type(exc).__name__})


def _worker_negative(ip,password,epoch):
    evidence={}
    dsn=_dsn(WORKER,password,ip)
    for name,statement in [
        ("direct_select","SELECT * FROM assistx_trace_fence_research.reservations"),
        ("direct_delete","DELETE FROM assistx_trace_fence_research.reservations"),
        ("rearm","UPDATE assistx_trace_fence_research.authority SET capacity=16"),
        ("release","SELECT assistx_trace_fence_research.release_exact(%s,%s,%s)"),
        ("drop","DROP TABLE assistx_trace_fence_research.reservations"),
    ]:
        try:
            with _open(dsn) as db:
                if name=="release":
                    db.execute(statement,(epoch,"a"*32,"forged"))
                else:
                    db.execute(statement)
            evidence[name]="UNSAFE_SUCCEEDED"
        except (psycopg.errors.InsufficientPrivilege,psycopg.errors.UndefinedFunction):
            evidence[name]="denied"
    assert set(evidence.values())=={"denied"},evidence
    return evidence


def run():
    ip=_address()
    admin_pw=os.environ["ASSISTX_TRACE_PG_ADMIN_PASSWORD"]
    worker_pw=os.environ["ASSISTX_TRACE_PG_WORKER_PASSWORD"]
    verifier_pw=os.environ["ASSISTX_TRACE_PG_VERIFIER_PASSWORD"]
    admin=_dsn("postgres",admin_pw,ip)
    epoch=str(uuid.uuid4())
    bootstrap_once(admin,worker_pw,verifier_pw,epoch)
    direct_denial=_worker_negative(ip,worker_pw,epoch)
    try:
        with _open(_dsn(WORKER,verifier_pw,ip)):raise AssertionError("PASSWORD_ROLE_SWAP_ACCEPTED")
    except psycopg.OperationalError:
        pass
    with _open(_dsn(WORKER,worker_pw,ip)) as db:
        invalid=db.execute(
            "SELECT * FROM assistx_trace_fence_research.admit(%s,%s,%s)",
            (str(uuid.uuid4()),uuid.uuid4().hex,"wrong-epoch")).fetchone()
    assert not invalid[0] and invalid[2]=="epoch-unavailable"

    ctx=mp.get_context("spawn")
    result={"schema":"assistx-pg-role-enforced-research-v1",
            "role_permissions":direct_denial,
            "scram_wrong_role_password_denied":True,
            "unknown_epoch_denied_inside_postgres":True,
            "capacity_enforced_inside_sql":True,
            "distributed_quorum_proven":False,
            "server_owned_neo4j_witness_proven":False,
            "production_access":False,
            "runs":[]}
    verifier_dsn=_dsn(VERIFIER,verifier_pw,ip)
    for attempts in (1,3,5,10):
        begin,physical=ctx.Event(),ctx.Event()
        outputs=ctx.Queue()
        processes=[ctx.Process(target=_worker,
             args=(ip,worker_pw,epoch,f"worker-{attempts}-{i}",begin,physical,outputs))
             for i in range(attempts)]
        for p in processes:p.start()
        begin.set()
        answers=[outputs.get(timeout=25) for _ in processes]
        assert not [a for a in answers if a.get("failure")],answers
        approved=[a for a in answers if a["accepted"]]
        assert len(approved)==min(3,attempts),answers
        physical.set()
        observed=0
        for n in range(35):
            with _open(admin) as db:
                observed=db.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE application_name='assistx_pg_privileged_read_probe' "
                    "AND state='active' AND query LIKE 'SELECT pg_sleep%'"
                ).fetchone()[0]
            if observed==len(approved):break
            time.sleep(.075)
        assert observed==len(approved), (observed,len(approved))
        for p in processes:
            p.join(8)
            if p.is_alive():p.kill();p.join(3)
            assert p.exitcode==0,p.exitcode
        # Release held by privileged verifier only AFTER read activity ends.
        with _open(verifier_dsn) as verify:
            occupied=verify.execute(
              "SELECT assistx_trace_fence_research.inspect(%s)",(epoch,)).fetchone()[0]
            assert occupied==len(approved),occupied
        # The privileged verifier has no access to worker's token through
        # SELECT: retrieve the exact fixture tokens only using the admin
        # test harness. A real independent verifier must bind Neo4j IDs.
        with _open(admin) as db:
            rows=db.execute(
                "SELECT token,query_ref FROM assistx_trace_fence_research.reservations "
                "ORDER BY query_ref").fetchall()
        assert len(rows)==len(approved)
        with _open(verifier_dsn) as verify:
            for token,ref in rows:
                assert verify.execute(
                    "SELECT assistx_trace_fence_research.release_exact(%s,%s,%s)",
                    (epoch,token,ref)).fetchone()[0] is True
                assert verify.execute(
                    "SELECT assistx_trace_fence_research.release_exact(%s,%s,%s)",
                    (epoch,token,ref)).fetchone()[0] is False
            assert verify.execute(
                "SELECT assistx_trace_fence_research.inspect(%s)",(epoch,)).fetchone()[0]==0
        result["runs"].append({"attempts":attempts,"accepted":len(approved),
            "denied":attempts-len(approved),"physically_observed_server_reads":observed,
            "release_only_verifier_role":True})
    print(json.dumps(result,indent=2,sort_keys=True))
    return result


if __name__=="__main__":
    run()
