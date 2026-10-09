#!/usr/bin/env python3
"""Test-only, externally fenced *mock* provider dispatch. Python stdlib only.

Research demonstration. No real provider client, credential, Mercury daemon,
federated consensus, mutual TLS, or quorum. Host this on ONE disposable x1
process and keep its SQLite state OUTSIDE copied issuer/coordinator state.
Only a LOCAL operator can change which node owns the current fence.
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

TEST_GROUP = "mercury-fencing-fixture-account"
MAX_BODY = 8192
IDENT = re.compile(r"^[A-Za-z0-9_-]{3,100}$")


def compact(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def signature(key: str, value) -> str:
    return hmac.new(bytes.fromhex(key), compact(value), hashlib.sha256).hexdigest()


def check_signature(key: str, value, mac: str) -> bool:
    return isinstance(mac, str) and bool(re.fullmatch(r"[a-f0-9]{64}", mac)) and (
        hmac.compare_digest(signature(key, value), mac)
    )


def database(db: str):
    conn = sqlite3.connect(db, timeout=3, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def audit(conn, value):
    row = conn.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    previous = row[0] if row else "0" * 64
    digest = hashlib.sha256(previous.encode() + compact(value)).hexdigest()
    conn.execute("INSERT INTO audit(prev,hash,value) VALUES(?,?,?)",
                 (previous, digest, compact(value).decode()))
    return digest


def initialize(db: str):
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    lock = open(db + ".exclusive.lock", "a+b")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        lock.close()
        raise
    conn = database(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS current_fence(
      singleton INTEGER PRIMARY KEY CHECK(singleton=1),
      generation INTEGER NOT NULL, issuer TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS replay(node TEXT NOT NULL, nonce TEXT NOT NULL,
      PRIMARY KEY(node,nonce));
    CREATE TABLE IF NOT EXISTS decisions(
      seq INTEGER PRIMARY KEY AUTOINCREMENT, node TEXT NOT NULL,
      job TEXT NOT NULL, fence INTEGER NOT NULL,
      allowed INTEGER NOT NULL, reason TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit(
      seq INTEGER PRIMARY KEY AUTOINCREMENT, prev TEXT NOT NULL,
      hash TEXT NOT NULL, value TEXT NOT NULL);
    """)
    conn.execute("INSERT OR IGNORE INTO current_fence VALUES(1,0,'none')")
    conn.close()
    return lock


def current(conn):
    generation, issuer = conn.execute(
        "SELECT generation,issuer FROM current_fence WHERE singleton=1"
    ).fetchone()
    return generation, issuer


def local_promote(db, issuer: str, expected: int):
    # No network API for leadership transfer. Manual test-only local action.
    if issuer not in ("node-x1", "node-xwing"):
        raise ValueError("not_allowed_issuer")
    conn = database(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        generation, _ = current(conn)
        if expected != generation:
            conn.rollback()
            raise ValueError("fencing_compare_and_swap_failed")
        next_gen = generation + 1
        conn.execute("UPDATE current_fence SET generation=?,issuer=? WHERE singleton=1",
                     (next_gen, issuer))
        digest = audit(conn, {"event":"local_promote","generation":next_gen,
                              "issuer":issuer,"at_ms":int(time.time()*1000)})
        conn.commit()
        return {"generation":next_gen,"issuer":issuer,"auditHead":digest}
    finally:
        conn.close()


def authorize(conn, config, envelope):
    data = envelope.get("data") if isinstance(envelope,dict) else None
    node = data.get("node") if isinstance(data,dict) else None
    key = config["test_keys"].get(node) if isinstance(node,str) else None
    if not key or not check_signature(key, data, envelope.get("mac")):
        return 403, {"error":"invalid_test_identity"}
    op = data.get("op")
    nonce = data.get("nonce")
    request = data.get("request")
    if op != "mock_dispatch" or not isinstance(request,dict) or not isinstance(nonce,str) \
        or not re.fullmatch(r"[0-9a-f-]{36}",nonce):
        return 400, {"error":"invalid_signed_payload"}
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO replay(node,nonce) VALUES(?,?)",(node,nonce))
    except sqlite3.IntegrityError:
        conn.rollback()
        return 409,{"error":"signed_replay_rejected"}
    except Exception:
        conn.rollback()
        raise
    try:
        generation, issuer = current(conn)
        claimed_generation = request.get("fence")
        job = request.get("job")
        model = request.get("model")
        provider = request.get("provider")
        bot = request.get("botId")
        group = request.get("upstreamGroup")
        policy = config["test_policy"].get(node,{})
        allowed_request = (
            isinstance(job,str) and bool(IDENT.fullmatch(job))
            and isinstance(claimed_generation,int) and not isinstance(claimed_generation,bool)
            and model == policy.get("model")
            and provider == policy.get("provider")
            and bot == policy.get("botId")
            and group == policy.get("upstreamGroup") == TEST_GROUP
        )
        # This is the whole downstream model-dispatch authorization decision.
        # Copied coordinator SQLite rows cannot change the proxy's own fence.
        permitted = bool(allowed_request and generation>0
                         and node == issuer and claimed_generation == generation)
        reason = "mock_dispatched" if permitted else (
            "fixture_policy_denied" if not allowed_request else "stale_or_wrong_issuer")
        conn.execute("INSERT INTO decisions(node,job,fence,allowed,reason) VALUES(?,?,?,?,?)",
                     (node,str(job)[:100],claimed_generation if isinstance(
                      claimed_generation,int) else -1,int(permitted),reason))
        head = audit(conn, {"event":"mock_dispatch","node":node,"job":str(job)[:100],
                            "claim":claimed_generation,"issuer":issuer,
                            "generation":generation,"allowed":permitted,
                            "reason":reason,"at_ms":int(time.time()*1000)})
        conn.commit()
        reply = {"op":op,"nonce":nonce,"permitted":permitted,"reason":reason,
                 "issuer":issuer,"generation":generation,"auditHead":head}
        return 200, {"data":reply,"mac":signature(key,reply)}
    except Exception:
        conn.rollback()
        raise


def server(args):
    config=json.loads(Path(args.config).read_text())
    if set(config) != {"test_keys","test_policy"}:
        raise ValueError("test_config_fields_invalid")
    for node,key in config["test_keys"].items():
        if node not in ("node-x1","node-xwing") or not re.fullmatch(r"[a-f0-9]{64}",key):
            raise ValueError("invalid_ephemeral_key")
    lock=initialize(args.db)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path!="/fixture/mock-dispatch":
                self.send_error(404);return
            length=int(self.headers.get("Content-Length","0"))
            if not 0<length<=MAX_BODY:
                self.send_error(413);return
            try:
                payload=json.loads(self.rfile.read(length))
                conn=database(args.db)
                try: code,result=authorize(conn,config,payload)
                finally: conn.close()
            except Exception:
                code,result=503,{"error":"fixture_proxy_unavailable"}
            wire=compact(result)
            self.send_response(code)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)
        def log_message(self,*_): return
    http=ThreadingHTTPServer((args.bind,args.port),Handler)
    print(json.dumps({"ready":True,"port":http.server_port,"pid":os.getpid(),
                      "fence":get_fence(args.db)}),flush=True)
    try: http.serve_forever(poll_interval=.05)
    finally:
        http.server_close()
        lock.close()


def get_fence(db):
    conn=database(db)
    try:
        generation,issuer=current(conn)
        return {"generation":generation,"issuer":issuer}
    finally:conn.close()


def client(args):
    key=Path(args.keyfile).read_text().strip()
    req={"job":args.job,"botId":args.bot_id,"provider":args.provider,
         "model":args.model,"upstreamGroup":args.group,"fence":args.fence}
    data={"node":args.node,"nonce":str(uuid.uuid4()),"op":"mock_dispatch","request":req}
    wire=compact({"data":data,"mac":signature(key,data)})
    request=Request(args.url+"/fixture/mock-dispatch",data=wire,
                    headers={"Content-Type":"application/json"},method="POST")
    try:
        with urlopen(request,timeout=1.5) as resp:
            answer=json.loads(resp.read(MAX_BODY)); status=resp.status
    except HTTPError as e:
        answer=json.loads(e.read(MAX_BODY));status=e.code
    except (URLError,OSError,TimeoutError) as e:
        print(json.dumps({"state":"unavailable_denied","node":args.node,
                          "reason":type(e).__name__}),flush=True)
        return 3
    response=answer.get("data",{}) if isinstance(answer,dict) else {}
    verified=(status==200 and isinstance(response,dict)
              and check_signature(key,response,answer.get("mac"))
              and response.get("nonce")==data["nonce"]
              and response.get("op")=="mock_dispatch")
    permitted=verified and response.get("permitted") is True
    print(json.dumps({"state":"mock_dispatch_allowed" if permitted else "denied",
                      "node":args.node,"fence":args.fence,
                      "verified":verified,"reason":response.get("reason") if verified else "unverified",
                      "proxyGeneration":response.get("generation") if verified else None,
                      "proxyIssuer":response.get("issuer") if verified else None,
                      "auditHead":response.get("auditHead") if verified else None}),flush=True)
    return 0 if verified else 4


def inspect(db):
    conn=database(db)
    try:
        rows=conn.execute("SELECT seq,prev,hash,value FROM audit ORDER BY seq").fetchall()
        last="0"*64
        for seq,prev,digest,serialized in rows:
            if prev!=last or hashlib.sha256(last.encode()+compact(json.loads(serialized))).hexdigest()!=digest:
                raise ValueError("invalid_hash_link_"+str(seq))
            last=digest
        allow,deny=conn.execute(
            "SELECT sum(allowed),sum(1-allowed) FROM decisions"
        ).fetchone()
        return {"verified":True,"events":len(rows),"head":last,
                "mock_dispatches":allow or 0,"denials":deny or 0,"fence":get_fence(db)}
    finally:conn.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    s=sub.add_parser("serve");s.add_argument("--db",required=True);s.add_argument("--config",required=True)
    s.add_argument("--bind",required=True);s.add_argument("--port",type=int,required=True)
    pr=sub.add_parser("promote");pr.add_argument("--db",required=True)
    pr.add_argument("--issuer",required=True);pr.add_argument("--expect",type=int,required=True)
    cl=sub.add_parser("call");cl.add_argument("--node",required=True)
    cl.add_argument("--keyfile",required=True);cl.add_argument("--url",required=True)
    cl.add_argument("--fence",type=int,required=True);cl.add_argument("--job",required=True)
    cl.add_argument("--bot-id",default="reviewer");cl.add_argument("--model",default="mock-model")
    cl.add_argument("--provider",default="fixture");cl.add_argument("--group",default=TEST_GROUP)
    i=sub.add_parser("inspect");i.add_argument("--db",required=True)
    a=p.parse_args()
    if a.command=="serve":server(a)
    elif a.command=="promote":print(json.dumps(local_promote(a.db,a.issuer,a.expect)))
    elif a.command=="call":sys.exit(client(a))
    else: print(json.dumps(inspect(a.db)))


if __name__=="__main__":
    main()
