#!/usr/bin/env python3
"""Disposable two-physical-host admission experiment, Python stdlib ONLY.

DO NOT deploy as fleet provider authority: plaintext HTTP, test-only keys,
no consensus, no issuer quorum, no billing knowledge, no SDK transport.
Bind only an explicit private interface, keep scratch state, stop after tests.
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

MAX_BODY = 16384
IDENT = re.compile(r"^[A-Za-z0-9_-]{3,128}$")
GROUP = "mercury-research-single-free-physical-group"


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def mac(key, payload):
    return hmac.new(bytes.fromhex(key), canon(payload), "sha256").hexdigest()


def valid_mac(key, payload, received):
    return isinstance(received, str) and bool(re.fullmatch(r"[0-9a-f]{64}", received)) and (
        hmac.compare_digest(mac(key, payload), received)
    )


def connect(db):
    con = sqlite3.connect(db, timeout=2.0, isolation_level=None)
    con.execute("PRAGMA busy_timeout=2000")
    con.execute("PRAGMA synchronous=FULL")
    return con


def audit(con, event):
    head = con.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    prior = head[0] if head else "0" * 64
    digest = hashlib.sha256(prior.encode() + canon(event)).hexdigest()
    con.execute("INSERT INTO audit(prev_hash,hash,event_json) VALUES(?,?,?)",
                (prior, digest, canon(event).decode()))
    return digest


def init_authority(db, ttl_ms):
    db = str(Path(db).resolve())
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    # Persistent file lock prevents two local processes owning one sqlite file.
    lock = open(db + ".lock", "a+b")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        lock.close()
        raise
    con = connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, val INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS leases(
      grp TEXT PRIMARY KEY, lease_id TEXT NOT NULL, node TEXT NOT NULL,
      task TEXT NOT NULL, epoch INTEGER NOT NULL, expires_ms INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS nonces (node TEXT NOT NULL, nonce TEXT NOT NULL,
      PRIMARY KEY(node,nonce));
    CREATE TABLE IF NOT EXISTS audit(
      seq INTEGER PRIMARY KEY AUTOINCREMENT, prev_hash TEXT NOT NULL,
      hash TEXT NOT NULL, event_json TEXT NOT NULL);
    """)
    con.execute("BEGIN IMMEDIATE")
    row = con.execute("SELECT val FROM meta WHERE key='epoch'").fetchone()
    epoch = (row[0] if row else 0) + 1
    con.execute("INSERT OR REPLACE INTO meta(key,val) VALUES('epoch',?)", (epoch,))
    audit(con, {"op":"boot","epoch":epoch,"at_ms":int(time.time()*1000),"ttl_ms":ttl_ms})
    con.commit()
    con.close()
    return lock, epoch


def handle(con, config, epoch, ttl_ms, envelope):
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    signature = envelope.get("mac") if isinstance(envelope, dict) else None
    if not isinstance(payload, dict):
        return (403, {"error":"bad_request"})
    node = payload.get("node")
    keys = config["test_keys"]
    key = keys.get(node) if isinstance(node, str) else None
    if not key or not valid_mac(key, payload, signature):
        return (403, {"error":"unauthenticated_fixture"})
    operation = payload.get("op")
    nonce = payload.get("nonce")
    requested = payload.get("request")
    now = int(time.time()*1000)
    if operation not in ("acquire","renew","release") or not isinstance(nonce,str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", nonce
    ) or not isinstance(requested,dict):
        return (400, {"error":"invalid_signed_payload"})
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO nonces(node,nonce) VALUES(?,?)", (node,nonce))
    except sqlite3.IntegrityError:
        con.rollback()
        return (409, {"error":"replayed_nonce"})
    except Exception:
        con.rollback()
        raise
    try:
        task = requested.get("task")
        bot_id = requested.get("botId")
        provider = requested.get("provider")
        model = requested.get("model")
        grp = requested.get("upstreamGroup")
        mi = requested.get("maxInputTokens")
        mo = requested.get("maxOutputTokens")
        policy = config["test_policy"].get(node, {})
        allowed = bool(
            isinstance(task,str) and IDENT.fullmatch(task)
            and isinstance(bot_id,str) and bot_id == policy.get("botId")
            and isinstance(provider,str) and provider == policy.get("provider")
            and isinstance(model,str) and model == policy.get("model")
            and grp == policy.get("upstreamGroup") == GROUP
            and isinstance(mi,int) and not isinstance(mi,bool) and 0 < mi <= policy.get("maxInputTokens",0)
            and isinstance(mo,int) and not isinstance(mo,bool) and 0 < mo <= policy.get("maxOutputTokens",0)
        )
        row = con.execute(
            "SELECT lease_id,node,task,epoch,expires_ms FROM leases WHERE grp=?", (GROUP,)
        ).fetchone()
        if row and row[4] <= now:
            con.execute("DELETE FROM leases WHERE grp=?", (GROUP,))
            row = None
        result = None
        reason = "policy_denied" if not allowed else "occupied_or_stale"
        if allowed and operation == "acquire" and row is None and payload.get("lease") is None:
            lease_id = str(uuid.uuid4())
            expiry = now + ttl_ms
            con.execute("INSERT INTO leases VALUES(?,?,?,?,?,?)",
                        (GROUP,lease_id,node,task,epoch,expiry))
            result = {"leaseId":lease_id,"taskId":task,"botId":bot_id,
                      "provider":provider,"model":model,"upstreamGroup":GROUP,
                      "epoch":epoch,"expiresAtMs":expiry}
            reason = "admitted"
        elif allowed and operation in ("renew","release") and row:
            lease = payload.get("lease")
            # Epoch fencing: a previous process generation cannot use old
            # leases after recovery. Previous seats stay reserved until TTL.
            match = isinstance(lease,dict) and row[1] == node and row[2] == task \
                and row[3] == epoch and lease.get("epoch") == epoch \
                and lease.get("leaseId") == row[0] and lease.get("taskId") == task
            if match and operation == "renew":
                expiry = now + ttl_ms
                con.execute("UPDATE leases SET expires_ms=? WHERE grp=?", (expiry,GROUP))
                result = {**lease,"expiresAtMs":expiry}
                reason = "renewed"
            elif match and operation == "release":
                con.execute("DELETE FROM leases WHERE grp=?", (GROUP,))
                result = True
                reason = "released"
        event = {"op":operation,"node":node,"task":task,"epoch":epoch,
                 "decision":reason,"at_ms":now,"upstreamGroup":GROUP}
        head = audit(con,event)
        con.commit()
        response = {"nonce":nonce,"op":operation,"result":result,
                    "decision":reason,"epoch":epoch,"auditHead":head}
        return (200,{"payload":response,"mac":mac(key,response)})
    except Exception:
        con.rollback()
        raise


def serve(args):
    config = json.loads(Path(args.config).read_text())
    if set(config) != {"test_keys","test_policy"}:
        raise ValueError("invalid_test_only_config")
    for node,key in config["test_keys"].items():
        if not IDENT.fullmatch(node) or not re.fullmatch(r"[a-f0-9]{64}",key):
            raise ValueError("bad_fixture_key")
    lock,epoch = init_authority(args.db,args.ttl_ms)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/fixture/lease":
                self.send_error(404); return
            size = int(self.headers.get("Content-Length",0))
            if size < 2 or size > MAX_BODY:
                self.send_error(413); return
            try:
                envelope = json.loads(self.rfile.read(size))
                with connect(args.db) as con:
                    code,body = handle(con,config,epoch,args.ttl_ms,envelope)
            except Exception:
                code,body = 503,{"error":"fixture_unavailable"}
            wire = canon(body)
            self.send_response(code)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)
        def log_message(self, *_):
            return
    server = ThreadingHTTPServer((args.bind,args.port),Handler)
    print(json.dumps({"event":"ready","epoch":epoch,"pid":os.getpid(),
                      "bind":args.bind,"port":server.server_port}),flush=True)
    try:
        server.serve_forever(poll_interval=.05)
    finally:
        server.server_close()
        lock.close()


def call(args):
    key = Path(args.keyfile).read_text().strip()
    lease = json.loads(Path(args.leasefile).read_text()) if args.leasefile else None
    request = {"task":args.task,"botId":args.bot_id,"provider":args.provider,"model":args.model,
               "upstreamGroup":args.group,"maxInputTokens":100,"maxOutputTokens":10}
    payload = {"node":args.node,"nonce":str(uuid.uuid4()),"op":args.op,
               "request":request,"lease":lease}
    signed = {"payload":payload,"mac":mac(key,payload)}
    wire = canon(signed)
    req = Request(args.url+"/fixture/lease",data=wire,method="POST",
                  headers={"Content-Type":"application/json"})
    try:
        with urlopen(req,timeout=args.timeout) as resp:
            answer = json.loads(resp.read(MAX_BODY))
            code = resp.status
    except HTTPError as e:
        answer = json.loads(e.read(MAX_BODY))
        code = e.code
    except (URLError,TimeoutError,OSError) as e:
        print(json.dumps({"state":"unavailable_denied","node":args.node,
                          "op":args.op,"reason":type(e).__name__}),flush=True)
        return 3
    accepted = False
    if code == 200:
        b = answer.get("payload",{})
        accepted = valid_mac(key,b,answer.get("mac")) and b.get("nonce")==payload["nonce"] \
            and b.get("op")==args.op
    result = answer.get("payload",{}).get("result") if accepted else None
    reason = answer.get("payload",{}).get("decision") if accepted else answer.get("error","response_unverified")
    state = "admitted" if isinstance(result,dict) else ("released" if result is True else "denied")
    if isinstance(result,dict) and args.savelease:
        Path(args.savelease).write_text(json.dumps(result))
    print(json.dumps({"state":state,"node":args.node,"op":args.op,
                      "decision":reason,"verified":bool(accepted),"http":code,
                      "epoch":answer.get("payload",{}).get("epoch") if accepted else None,
                      "auditHead":answer.get("payload",{}).get("auditHead") if accepted else None}),flush=True)
    return 0 if accepted else 4


def inspect(args):
    con=connect(args.db)
    rows=con.execute("SELECT seq,prev_hash,hash,event_json FROM audit ORDER BY seq").fetchall()
    prev="0"*64
    for seq,old,digest,json_event in rows:
        body=json.loads(json_event)
        if old!=prev or hashlib.sha256(prev.encode()+canon(body)).hexdigest()!=digest:
            raise ValueError("broken_audit_chain_at_"+str(seq))
        prev=digest
    print(json.dumps({"audit_verified":True,"events":len(rows),
                      "final_head":prev,"epochs":[r[0] for r in con.execute(
                          "SELECT val FROM meta WHERE key='epoch'")],
                      "live_seats":con.execute("SELECT count(*) FROM leases").fetchone()[0]}))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    a=sub.add_parser("serve");a.add_argument("--config",required=True);a.add_argument("--db",required=True)
    a.add_argument("--bind",required=True);a.add_argument("--port",type=int,required=True)
    a.add_argument("--ttl-ms",type=int,default=4000)
    c=sub.add_parser("call");c.add_argument("--url",required=True);c.add_argument("--node",required=True)
    c.add_argument("--keyfile",required=True);c.add_argument("--op",choices=["acquire","renew","release"],required=True)
    c.add_argument("--task",required=True);c.add_argument("--bot-id",default="reviewer");c.add_argument("--provider",default="fixture")
    c.add_argument("--model",default="mock-model");c.add_argument("--group",default=GROUP)
    c.add_argument("--leasefile");c.add_argument("--savelease");c.add_argument("--timeout",type=float,default=1)
    i=sub.add_parser("inspect");i.add_argument("--db",required=True)
    a=p.parse_args()
    if a.command=="serve":
        if a.ttl_ms < 500 or a.ttl_ms > 120000:
            raise SystemExit("invalid_test_ttl")
        serve(a)
    elif a.command=="call":
        sys.exit(call(a))
    else: inspect(a)


if __name__=="__main__":
    main()
