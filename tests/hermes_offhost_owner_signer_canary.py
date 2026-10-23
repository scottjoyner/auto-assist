#!/usr/bin/env python3
"""Off-host synthetic signed gateway -> isolated exact AssistX Task-create proof.

Uses Lenovo only for one ephemeral in-memory Ed25519 signing process.
Uses pinned OpenSSH host key and batch-only noninteractive transport.
Never writes a private signing key to disk or ships private bytes to x1-370.
API on x1-370 runs ONLY an isolated FastAPI TestClient and in-memory fake
Neo4j sessions; production API and Hermes remain untouched.

Shared Tailscale/SSH administration means this is NOT independent custody.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from hmac import compare_digest
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import tempfile
import time
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from assistx.hermes_launch_gate import LaunchDenied
from assistx.owner_gateway_provenance import (
    AUDIENCE, OwnerGatewayDenied, SignedGatewayOwnerMiddleware,
    verify_attestation, _consume_nonce,
)
from assistx.neo4j_client import Neo4jClient
from test_owner_authority_contract import MemoryStore
from test_owner_gateway_actual_task_route import exact_task_handler, IDENTITIES

EXPECTED_PRODUCTION="sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307"
REMOTE=r'''
import base64,json,os,sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
b64=lambda raw:base64.urlsafe_b64encode(raw).decode().rstrip("=")
d=json.loads(base64.b64decode(sys.argv[1],validate=True))
if len(d)!=3 or [v["owner_subject"] for v in d]!=["person-a","person-b","person-b"]:
    raise SystemExit("unsupported synthetic principals")
signer=Ed25519PrivateKey.generate()
public=signer.public_key().public_bytes(
    serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
tokens=[]
for p in d:
    raw=json.dumps(p,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()
    tokens.append(b64(raw)+"."+b64(signer.sign(raw)))
print(json.dumps({"public_pem":public.decode(),"signed":tokens,
                  "remote_pid":os.getpid(),"private_key_exported":False}))
'''

class UnsafeCanary(RuntimeError):
    pass


def must(condition, reason):
    if not condition:
        raise UnsafeCanary(reason)


def shell(command,timeout=20):
    p=subprocess.run(command,capture_output=True,text=True,timeout=timeout)
    must(p.returncode==0,"strict remote signer command failed")
    return p.stdout.strip()


def payloads():
    now=int(time.time())
    raw_body=json.dumps({
        "title":"isolated synthetic task",
        "task_type":"swarm_task",
        "idempotency_key":"shared-two-owner-idempotency",
        "payload":{"data":"no-model-no-corpus"}},
        sort_keys=True,separators=(",",":")).encode()
    claims=[]
    for who in ("person-a","person-b","person-b"):
        claims.append({
            "schema":1,"aud":AUDIENCE,"owner_issuer":"synthetic-lenovo-idp",
            "owner_subject":who,"method":"POST","path":"/api/tasks",
            "body_sha256":hashlib.sha256(raw_body).hexdigest(),
            "issued_at":now,"expires_at":now+30,
            "nonce":secrets.token_hex(32),
        })
    return raw_body,claims


def remote_sign(claims):
    compact=base64.b64encode(json.dumps(claims,separators=(",",":")).encode()).decode()
    cmd=["python3","-B","-c",REMOTE,compact]
    quoted=" ".join(shlex.quote(part) for part in cmd)
    args=["ssh","-n","-T","-o","BatchMode=yes",
          "-o","StrictHostKeyChecking=yes",
          "-o","ConnectTimeout=4","-o","ConnectionAttempts=1",
          "lenovo",quoted]
    raw=shell(args,timeout=19)
    must(len(raw)<12000, "unexpected remote result length")
    result=json.loads(raw)
    must(result.get("private_key_exported") is False
         and type(result.get("remote_pid")) is int,
         "remote signer response not bounded to public outputs")
    must(type(result.get("public_pem")) is str
         and result["public_pem"].startswith("-----BEGIN PUBLIC KEY-----"),
         "missing remote public-key attestation")
    tokens=result.get("signed")
    must(type(tokens) is list and len(tokens)==3
         and all(type(v) is str and len(v)<2048 for v in tokens),
         "invalid remote synthetic signature list")
    return result


def isolated_client():
    store=MemoryStore()
    neo=Neo4jClient.__new__(Neo4jClient)
    neo._session=store.session
    callbacks=[]
    class DisposableNeo:
        def create_owned_task(self,**kwargs):
            return neo.create_owned_task(**kwargs)
        def close(self):
            pass
    def test_basic_user(request:Request):
        raw=request.headers.get("Authorization","")
        if not raw.startswith("Basic "):
            raise HTTPException(status_code=401,detail="synthetic auth required")
        try:
            actual=base64.b64decode(raw[6:],validate=True).decode()
        except (ValueError,UnicodeError):
            raise HTTPException(status_code=401,detail="synthetic auth required") from None
        for person,password in IDENTITIES.items():
            if compare_digest(actual,f"{person}:{password}"):
                return person
        raise HTTPException(status_code=401,detail="synthetic auth required")
    app=FastAPI()
    app.add_api_route("/api/tasks",
                      exact_task_handler(test_basic_user,lambda:DisposableNeo()),
                      methods=["POST"])
    app.add_middleware(SignedGatewayOwnerMiddleware)
    return TestClient(app),store,callbacks


def submit(client,body,token,person):
    auth=base64.b64encode((person+":"+IDENTITIES[person]).encode()).decode()
    return client.post("/api/tasks",content=body,headers={
        "Authorization":"Basic "+auth,"Content-Type":"application/json",
        "X-AssistX-Signed-Owner":token,
    })


def execute():
    must(os.uname().nodename=="x1-370" and os.geteuid()==1000,
         "x1-370 nonroot operator only")
    prod_cmd=["docker","inspect","-f","{{.Image}} {{.State.Running}}",
              "assistx-hermes-adapter"]
    before=shell(prod_cmd,timeout=10)
    must(before==EXPECTED_PRODUCTION+" true","production identity not expected")
    body,claims=payloads()
    signed=remote_sign(claims)
    with tempfile.TemporaryDirectory(prefix="assistx-owner-offhost-") as home:
        path=Path(home)
        path.chmod(0o700)
        verify_file=path/"lenovo-ephemeral-gateway.pub"
        verify_file.write_text(signed["public_pem"],encoding="ascii")
        verify_file.chmod(0o644)
        old_key=os.environ.get("ASSISTX_OWNER_GATEWAY_VERIFY_KEY_FILE")
        old_nonce=os.environ.get("ASSISTX_OWNER_GATEWAY_NONCE_DB")
        old_enable=os.environ.get("ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY")
        old_owner=os.environ.get("ASSISTX_REQUIRE_OWNER_BINDING")
        try:
            os.environ["ASSISTX_OWNER_GATEWAY_VERIFY_KEY_FILE"]=str(verify_file)
            os.environ["ASSISTX_OWNER_GATEWAY_NONCE_DB"]=str(path/"nonces.sqlite")
            os.environ["ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY"]="true"
            os.environ["ASSISTX_REQUIRE_OWNER_BINDING"]="true"
            for index,token in enumerate(signed["signed"]):
                checked=verify_attestation(token,method="POST",
                                           path="/api/tasks",request_body=body)
                must(checked["owner_subject"]==claims[index]["owner_subject"],
                     "signed Lenovo principal mismatch")
            client,store,events=isolated_client()
            import assistx.neo4j_client as neo_module
            with patch.object(neo_module,"notify_task_created",
                              side_effect=lambda **kw:events.append(kw)):
                first=submit(client,body,signed["signed"][0],"person-a")
                second=submit(client,body,signed["signed"][1],"person-b")
                spoofed=submit(client,body,signed["signed"][2],"person-a")
                replay=submit(client,body,signed["signed"][0],"person-a")
            must(first.status_code==200 and second.status_code==200,
                 "distinct owners not accepted with off-host attestations")
            must(first.json()["task_id"]!=second.json()["task_id"],
                 "owner-scoped idempotency collided")
            must(spoofed.status_code==409 and replay.status_code==401,
                 "cross-owner or replay was not rejected")
            must(store.writes==2 and len(events)==2,
                 "unexpected synthetic Task writes")
            tamper_denied=False
            try:
                verify_attestation(signed["signed"][0],method="POST",
                                   path="/api/tasks",request_body=b"forged")
            except OwnerGatewayDenied:
                tamper_denied=True
            must(tamper_denied,"off-host signature not bound to exact body")
            report={
                "decision":"HOLD",
                "offhost_signer":"Lenovo",
                "offhost_signing_private_key_exported":False,
                "offhost_signing_key_held_in_ephemeral_process_memory":True,
                "independent_administrative_custody":False,
                "separate_signed_owners_accepted":True,
                "one_idempotency_key_two_distinct_owner_tasks":True,
                "cross_owner_spoof_rejected":True,
                "replay_denied":True,
                "request_body_tamper_denied":True,
                "test_only_neo4j_writes":store.writes,
                "production_task_mutations":0,
                "real_provider_model_or_hermes_requests":0,
                "nonprod_fixtures_deleted":True,
                "production_modified":False,
            }
        finally:
            for key,value in [
                ("ASSISTX_OWNER_GATEWAY_VERIFY_KEY_FILE",old_key),
                ("ASSISTX_OWNER_GATEWAY_NONCE_DB",old_nonce),
                ("ASSISTX_ENABLE_SIGNED_OWNER_GATEWAY",old_enable),
                ("ASSISTX_REQUIRE_OWNER_BINDING",old_owner),
            ]:
                if value is None:
                    os.environ.pop(key,None)
                else:
                    os.environ[key]=value
    must(shell(prod_cmd,timeout=10)==before,
         "production container identity changed during rehearsal")
    return report


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--physical-disposable-lenovo",action="store_true")
    a=p.parse_args()
    result=execute() if a.physical_disposable_lenovo else {
        "decision":"HOLD","physical_executed":False,
        "production_modified":False,
    }
    print(json.dumps(result,sort_keys=True))
