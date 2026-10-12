#!/usr/bin/env python3
"""Single-authority, zero-provider-transport in-flight quarantine fixture.

DO NOT use for hosted model routing. No consensus, HTTPS/mTLS, vendor keys,
trusted process attestation, real cancellation, or safe replicated failover.
For a research proof, all mock upstream work MUST go via ONE server instance.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GROUP = "mercury-test-only-physical-upstream"
ID = re.compile(r"^[A-Za-z0-9_-]{3,100}$")
MAX_BODY = 8192


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sign(key, value):
    return hmac.new(bytes.fromhex(key), canon(value), hashlib.sha256).hexdigest()


def authentic(key, value, mac):
    return isinstance(mac, str) and bool(re.fullmatch(r"[a-f0-9]{64}", mac)) and (
        hmac.compare_digest(sign(key, value), mac)
    )


def connect(db):
    dbcon = sqlite3.connect(db, timeout=3, isolation_level=None)
    dbcon.execute("PRAGMA busy_timeout=3000")
    dbcon.execute("PRAGMA synchronous=FULL")
    return dbcon


def append_audit(dbcon, record):
    prior = dbcon.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    prev = prior[0] if prior else "0"*64
    digest = hashlib.sha256(prev.encode() + canon(record)).hexdigest()
    dbcon.execute("INSERT INTO audit(prev,hash,event) VALUES(?,?,?)",
                  (prev, digest, canon(record).decode()))
    return digest


def initialize(db):
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    lock = open(db + ".exclusive.lock", "a+b")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        lock.close()
        raise
    con = connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS controller(
      singleton INTEGER PRIMARY KEY CHECK(singleton=1),
      generation INTEGER NOT NULL, issuer TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS slots(
      grp TEXT PRIMARY KEY, run_id TEXT NOT NULL, job TEXT NOT NULL,
      node TEXT NOT NULL, generation INTEGER NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('active','quarantined')),
      quarantined_at INTEGER);
    CREATE TABLE IF NOT EXISTS nonces(
      node TEXT NOT NULL, nonce TEXT NOT NULL, PRIMARY KEY(node,nonce));
    CREATE TABLE IF NOT EXISTS receipts(
      nonce TEXT PRIMARY KEY, run_id TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit(
      seq INTEGER PRIMARY KEY AUTOINCREMENT,
      prev TEXT NOT NULL, hash TEXT NOT NULL, event TEXT NOT NULL);
    """)
    con.execute("BEGIN IMMEDIATE")
    con.execute("INSERT OR IGNORE INTO controller VALUES(1,0,'none')")
    # Any non-acknowledged work on boot has an uncertain outcome. Fail closed.
    now=int(time.time()*1000)
    pending=con.execute("SELECT count(*) FROM slots WHERE state='active'").fetchone()[0]
    con.execute("UPDATE slots SET state='quarantined',quarantined_at=? WHERE state='active'",
                (now,))
    append_audit(con, {"event":"boot","atMs":now,"quarantinedActive":pending})
    con.commit()
    con.close()
    return lock


def state(con):
    return con.execute("SELECT generation,issuer FROM controller WHERE singleton=1").fetchone()


def promote(db, issuer, expect):
    if issuer not in ("node-x1","node-xwing"):
        raise ValueError("invalid_fixture_issuer")
    c=connect(db)
    try:
        c.execute("BEGIN IMMEDIATE")
        gen,_=state(c)
        if gen != expect:
            c.rollback()
            raise ValueError("stale_promotion_compare_and_swap")
        next_gen=gen+1
        now=int(time.time()*1000)
        active=c.execute("SELECT count(*) FROM slots WHERE state='active'").fetchone()[0]
        c.execute("UPDATE slots SET state='quarantined',quarantined_at=? WHERE state='active'",
                  (now,))
        c.execute("UPDATE controller SET generation=?,issuer=? WHERE singleton=1",
                  (next_gen, issuer))
        head=append_audit(c,{"event":"promote","generation":next_gen,"issuer":issuer,
                             "quarantinedActive":active,"atMs":now})
        c.commit()
        return {"generation":next_gen,"issuer":issuer,
                "quarantinedActive":active,"auditHead":head}
    finally: c.close()


def transact(con, config, envelope):
    data=envelope.get("data") if isinstance(envelope,dict) else None
    node=data.get("node") if isinstance(data,dict) else None
    key=config["test_keys"].get(node) if isinstance(node,str) else None
    if not key or not authentic(key,data,envelope.get("mac")):
        return 403,{"error":"bad_fixture_signature"}
    op=data.get("op")
    nonce=data.get("nonce")
    req=data.get("request")
    if op not in ("begin","finish","cancel_ack") or not isinstance(req,dict) \
       or not isinstance(nonce,str) or not re.fullmatch(r"[a-f0-9-]{36}",nonce):
        return 400,{"error":"bad_signed_input"}
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO nonces(node,nonce) VALUES(?,?)",(node,nonce))
    except sqlite3.IntegrityError:
        con.rollback()
        return 409,{"error":"replayed_signed_request"}
    except Exception:
        con.rollback()
        raise
    try:
        gen,issuer=state(con)
        task=req.get("job")
        claim=req.get("fence")
        policy=config["test_policy"].get(node,{})
        valid=(isinstance(task,str) and bool(ID.fullmatch(task))
               and type(claim) is int and claim>0
               and req.get("botId")==policy.get("botId")
               and req.get("provider")==policy.get("provider")
               and req.get("model")==policy.get("model")
               and req.get("upstreamGroup")==policy.get("upstreamGroup")==GROUP)
        slot=con.execute(
          "SELECT run_id,job,node,generation,state FROM slots WHERE grp=?",(GROUP,)
        ).fetchone()
        result=None
        reason="policy_denied" if not valid else "stale_issuer_or_fence"
        if valid:
            if op=="begin" and claim==gen and node==issuer:
                if slot is None:
                    run_id=str(uuid.uuid4())
                    con.execute("INSERT INTO slots VALUES(?,?,?,?,?,?,?)",
                                (GROUP,run_id,task,node,gen,"active",None))
                    result={"runId":run_id,"job":task,"fence":gen,"state":"active"}
                    reason="mock_started"
                else:
                    reason="capacity_held_or_uncertain"
            elif op=="finish" and slot and claim==gen and node==issuer:
                if slot[2]==node and slot[3]==claim and slot[4]=="active" \
                   and req.get("runId")==slot[0] and task==slot[1]:
                    con.execute("DELETE FROM slots WHERE grp=?",(GROUP,))
                    result=True
                    reason="finished_current_mock_call"
                else:
                    reason="not_current_active_owner"
            elif op=="cancel_ack" and slot and req.get("runId")==slot[0] \
                    and slot[2]==node and slot[1]==task:
                # A stale worker's self-report does not prove vendor termination.
                # The upstream slot stays quarantined until independent receipt.
                reason="worker_ack_not_independent_proof"
        now=int(time.time()*1000)
        head=append_audit(con,{"event":op,"node":node,"job":str(task)[:100],
                               "claim":claim,"generation":gen,"issuer":issuer,
                               "reason":reason,"atMs":now})
        con.commit()
        payload={"op":op,"nonce":nonce,"result":result,"reason":reason,
                 "generation":gen,"issuer":issuer,"auditHead":head}
        return 200,{"data":payload,"mac":sign(key,payload)}
    except Exception:
        con.rollback()
        raise


def make_witness(key, run_id, generation, observed_at=None):
    # Synthetic OFFLINE fixture evidence: not actual vendor abort telemetry.
    receipt={"runId":run_id,"generation":generation,
             "observedAtMs":observed_at or int(time.time()*1000),
             "observation":"mock_worker_confirmed_stopped","nonce":str(uuid.uuid4())}
    return {"receipt":receipt,"mac":sign(key,receipt)}


def reconcile(db, receipt_path, keyfile):
    key=Path(keyfile).read_text().strip()
    envelope=json.loads(Path(receipt_path).read_text())
    receipt=envelope.get("receipt")
    if not isinstance(receipt,dict) or not authentic(key,receipt,envelope.get("mac")):
        raise ValueError("invalid_fixture_witness")
    if receipt.get("observation")!="mock_worker_confirmed_stopped" \
       or not isinstance(receipt.get("runId"),str) \
       or not isinstance(receipt.get("nonce"),str):
        raise ValueError("unexpected_fixture_observation")
    con=connect(db)
    try:
        con.execute("BEGIN IMMEDIATE")
        gen,_=state(con)
        slot=con.execute("SELECT run_id,state,quarantined_at FROM slots WHERE grp=?",
                         (GROUP,)).fetchone()
        if not slot or slot[1]!="quarantined" or slot[0]!=receipt["runId"] \
           or receipt.get("generation")!=gen \
           or type(receipt.get("observedAtMs")) is not int \
           or receipt["observedAtMs"] < slot[2] \
           or receipt["observedAtMs"] > int(time.time()*1000)+1000:
            con.rollback()
            raise ValueError("witness_not_bound_to_current_quarantine")
        con.execute("INSERT INTO receipts(nonce,run_id) VALUES(?,?)",
                    (receipt["nonce"],receipt["runId"]))
        con.execute("DELETE FROM slots WHERE grp=?",(GROUP,))
        head=append_audit(con,{"event":"reconcile","generation":gen,
                               "runId":receipt["runId"],"witnessNonce":receipt["nonce"],
                               "atMs":int(time.time()*1000)})
        con.commit()
        return {"reconciled":True,"generation":gen,"auditHead":head}
    finally:con.close()


def inspect(db):
    con=connect(db)
    try:
        prev="0"*64
        rows=con.execute("SELECT seq,prev,hash,event FROM audit ORDER BY seq").fetchall()
        for seq,old,digest,raw in rows:
            if prev!=old or hashlib.sha256(prev.encode()+canon(json.loads(raw))).hexdigest()!=digest:
                raise ValueError("broken_audit_chain_"+str(seq))
            prev=digest
        gen,issuer=state(con)
        slot=con.execute("SELECT run_id,job,node,generation,state FROM slots WHERE grp=?",
                         (GROUP,)).fetchone()
        return {"verified":True,"events":len(rows),"head":prev,"generation":gen,
                "issuer":issuer,"slot":dict(zip(
                  ("runId","job","node","fence","state"),slot)) if slot else None}
    finally:con.close()


def serve(args):
    config=json.loads(Path(args.config).read_text())
    if set(config)!={"test_keys","test_policy"}:
        raise ValueError("invalid_fixture_config")
    for node,key in config["test_keys"].items():
        if node not in ("node-x1","node-xwing") or not re.fullmatch(r"[a-f0-9]{64}",key):
            raise ValueError("invalid_fixture_key")
    lock=initialize(args.db)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path!="/fixture/work":
                self.send_error(404); return
            n=int(self.headers.get("Content-Length","0"))
            if n<1 or n>MAX_BODY:
                self.send_error(413);return
            try:
                data=json.loads(self.rfile.read(n))
                con=connect(args.db)
                try:code,response=transact(con,config,data)
                finally:con.close()
            except Exception:
                code,response=503,{"error":"fixture_unavailable"}
            body=canon(response)
            self.send_response(code)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,*_):return
    srv=ThreadingHTTPServer((args.bind,args.port),Handler)
    print(json.dumps({"ready":True,"port":args.port,"pid":os.getpid(),
                      "state":inspect(args.db)}),flush=True)
    try:srv.serve_forever(poll_interval=.05)
    finally:
        srv.server_close()
        lock.close()


def client(args):
    key=Path(args.keyfile).read_text().strip()
    data={"node":args.node,"nonce":str(uuid.uuid4()),"op":args.op,
          "request":{"job":args.job,"botId":args.bot_id,"provider":args.provider,
                     "model":args.model,"upstreamGroup":args.group,
                     "fence":args.fence,"runId":args.run_id}}
    wire=canon({"data":data,"mac":sign(key,data)})
    req=Request(args.url+"/fixture/work",data=wire,
                headers={"Content-Type":"application/json"},method="POST")
    try:
        with urlopen(req,timeout=1.5) as response:
            body=json.loads(response.read(MAX_BODY))
            status=response.status
    except HTTPError as e:
        status=e.code;body=json.loads(e.read(MAX_BODY))
    except (URLError,TimeoutError,OSError) as e:
        print(json.dumps({"state":"unavailable_denied","reason":type(e).__name__,
                          "node":args.node,"op":args.op}),flush=True)
        return 3
    reply=body.get("data",{}) if isinstance(body,dict) else {}
    verified=status==200 and isinstance(reply,dict) \
        and authentic(key,reply,body.get("mac")) \
        and reply.get("nonce")==data["nonce"] and reply.get("op")==args.op
    result=reply.get("result") if verified else None
    outcome=("mock_started" if isinstance(result,dict) else
             "finished" if result is True else "denied")
    if isinstance(result,dict) and args.save_run:
        Path(args.save_run).write_text(json.dumps(result))
    print(json.dumps({"state":outcome,"verified":verified,"node":args.node,
                      "op":args.op,"reason":reply.get("reason") if verified else "unverified",
                      "result":result,"generation":reply.get("generation") if verified else None,
                      "head":reply.get("auditHead") if verified else None}),flush=True)
    return 0 if verified else 4


def main():
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest="command",required=True)
    s=subs.add_parser("serve");s.add_argument("--db",required=True)
    s.add_argument("--config",required=True);s.add_argument("--bind",required=True)
    s.add_argument("--port",type=int,required=True)
    pr=subs.add_parser("promote");pr.add_argument("--db",required=True)
    pr.add_argument("--issuer",required=True);pr.add_argument("--expect",type=int,required=True)
    cl=subs.add_parser("call");cl.add_argument("--url",required=True)
    cl.add_argument("--node",required=True);cl.add_argument("--keyfile",required=True)
    cl.add_argument("--op",choices=("begin","finish","cancel_ack"),required=True)
    cl.add_argument("--job",required=True);cl.add_argument("--fence",type=int,required=True)
    cl.add_argument("--run-id");cl.add_argument("--save-run")
    cl.add_argument("--model",default="mock-model");cl.add_argument("--provider",default="fixture")
    cl.add_argument("--bot-id",default="reviewer");cl.add_argument("--group",default=GROUP)
    w=subs.add_parser("witness");w.add_argument("--keyfile",required=True)
    w.add_argument("--run-id",required=True);w.add_argument("--generation",type=int,required=True)
    w.add_argument("--output",required=True)
    r=subs.add_parser("reconcile");r.add_argument("--db",required=True)
    r.add_argument("--keyfile",required=True);r.add_argument("--receipt",required=True)
    i=subs.add_parser("inspect");i.add_argument("--db",required=True)
    a=p.parse_args()
    if a.command=="serve":serve(a)
    elif a.command=="promote":print(json.dumps(promote(a.db,a.issuer,a.expect)))
    elif a.command=="call":sys.exit(client(a))
    elif a.command=="witness":
        key=Path(a.keyfile).read_text().strip()
        Path(a.output).write_text(json.dumps(make_witness(key,a.run_id,a.generation)))
        print(json.dumps({"fixture_witness_written":True}))
    elif a.command=="reconcile":
        print(json.dumps(reconcile(a.db,a.receipt,a.keyfile)))
    else:print(json.dumps(inspect(a.db)))


if __name__=="__main__":
    main()
