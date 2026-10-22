#!/usr/bin/env python3
"""Disposable Lenovo SQLite witness restart proof. NEVER production custody.

Identical reviewed Python sources execute on Lenovo from SSH command memory;
the remote ephemeral SQLite ledger and TEST-ONLY signing key stay in a private
temporary directory across short-lived SSH processes, then are destroyed.
Shared SSH control can roll back both: independence remains unproven.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess

from assistx.hermes_high_water import verify_receipt, canonical

ROOT=Path(__file__).resolve().parents[1]
LEDGER=ROOT/"src/assistx/hermes_witness_ledger.py"
HIGH=ROOT/"src/assistx/hermes_high_water.py"
EXPECTED_LEDGER="49430eee39381092511bd137b11947b8e93b5e24"
EXPECTED_HIGH="ed8992eef765006cb6a12def4553b04d0129ecbe"
EXPECTED_IMAGE="sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307"
REMOTE_PREFIX="/tmp/og-hermes-ledger-"
REMOTE_CODE=r'''
import base64,json,os,re,secrets,shutil,stat,sys,tempfile,types
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
pkg=types.ModuleType("assistx");pkg.__path__=[];sys.modules["assistx"]=pkg
def load(name,raw):
    module=types.ModuleType(name);sys.modules[name]=module
    exec(compile(base64.b64decode(raw),name,"exec"),module.__dict__)
    return module
load("assistx.hermes_high_water",sys.argv[1])
ledger=load("assistx.hermes_witness_ledger",sys.argv[2])
action=sys.argv[3]
owner="research-owned-hermes"
cp2="2"*64;manifest2="b"*64
if action=="provision":
    directory=tempfile.mkdtemp(prefix="og-hermes-ledger-",dir="/tmp")
    path=Path(directory)
    signer=Ed25519PrivateKey.generate()
    private=signer.private_bytes(serialization.Encoding.Raw,
             serialization.PrivateFormat.Raw,serialization.NoEncryption())
    fd=os.open(str(path/"signer.key"),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,"wb") as stream:
        stream.write(private);stream.flush();os.fsync(stream.fileno())
    os.chmod(path/"signer.key",0o600)
    with ledger.ResearchHighWaterLedger(str(path/"ledger.sqlite")) as db:
        db.publish(owner=owner,generation=2,
                   checkpoint_sha256=cp2,manifest_sha256=manifest2)
    pub=signer.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    print(json.dumps({"directory":directory,"public":pub.hex(),
                      "pid":os.getpid(),"generation":2}),flush=True)
else:
    directory=sys.argv[4]
    if not re.fullmatch(r"/tmp/og-hermes-ledger-[A-Za-z0-9_-]{6,24}",directory):
        raise RuntimeError("untrusted disposable path")
    path=Path(directory)
    meta=path.lstat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid!=os.geteuid() or meta.st_mode&0o077:
        raise RuntimeError("untrusted private storage custody")
    if action=="cleanup":
        shutil.rmtree(path)
        print(json.dumps({"removed":True,"pid":os.getpid()}),flush=True)
    else:
        meta=(path/"signer.key").lstat()
        if not stat.S_ISREG(meta.st_mode) or meta.st_mode&0o077 or meta.st_uid!=os.geteuid():
            raise RuntimeError("test key custody violation")
        signer=Ed25519PrivateKey.from_private_bytes((path/"signer.key").read_bytes())
        with ledger.ResearchHighWaterLedger(str(path/"ledger.sqlite")) as db:
            if action=="challenge":
                request=base64.b64decode(sys.argv[5],validate=True)
                response=db.respond(raw_request=request,signer=signer)
                print(json.dumps({"receipt":response.decode("ascii"),
                                  "pid":os.getpid()}),flush=True)
            elif action=="reject-old-publish":
                try:
                    db.publish(owner=owner,generation=1,
                               checkpoint_sha256="1"*64,manifest_sha256="a"*64)
                except ledger.LedgerDenied:
                    print(json.dumps({"rollback_denied":True,"pid":os.getpid()}),flush=True)
                else:
                    raise RuntimeError("rollback was erroneously admitted")
            elif action=="revoke":
                db.revoke(owner)
                print(json.dumps({"revoked":True,"pid":os.getpid()}),flush=True)
            else: raise RuntimeError("unsupported remote action")
'''

class UnsafeCanary(RuntimeError):
    pass

def need(ok,why):
    if not ok:raise UnsafeCanary(why)

def shell(cmd,timeout=12):
    p=subprocess.run(cmd,text=True,capture_output=True,timeout=timeout,check=False)
    need(p.returncode==0,"disposable strict SSH witness command failed")
    return p.stdout.strip()

def remote_payload():
    for source,expected in ((LEDGER,EXPECTED_LEDGER),(HIGH,EXPECTED_HIGH)):
        need(subprocess.run(["git","-C",str(ROOT),"hash-object",str(source)],
            capture_output=True,text=True,timeout=6).stdout.strip()==expected,
             "unreviewed off-host witness source")
    return (base64.b64encode(HIGH.read_bytes()).decode(),
            base64.b64encode(LEDGER.read_bytes()).decode())

def remote(payload,action,path=None,request=None):
    cmd=["python3","-B","-c",REMOTE_CODE,*payload,action]
    if path is not None: cmd.append(path)
    if request is not None:cmd.append(base64.b64encode(canonical(request)).decode())
    # Each call runs a fresh process on Lenovo, never a resident service.
    quoted=" ".join(shlex.quote(s) for s in cmd)
    args=["ssh","-n","-T","-o","BatchMode=yes",
          "-o","StrictHostKeyChecking=yes","-o","ConnectTimeout=4",
          "-o","ConnectionAttempts=1","lenovo",quoted]
    return json.loads(shell(args,18))

def challenge(generation:int):
    return {"schema":1,"nonce":secrets.token_hex(32),
            "owner":"research-owned-hermes",
            "generation":generation,
            "checkpoint_sha256":str(generation)*64,
            "manifest_sha256":("b" if generation==2 else "a")*64}

def physical():
    need(os.uname().nodename=="x1-370" and os.geteuid()==1000,
         "only x1-370 nonroot operator may run canary")
    production=["docker","inspect","-f","{{.Image}} {{.State.Running}}",
                "assistx-hermes-adapter"]
    before=shell(production)
    need(before==EXPECTED_IMAGE+" true","running production identity changed")
    payload=remote_payload()
    path=None
    try:
        created=remote(payload,"provision")
        path=created["directory"]
        need(path.startswith(REMOTE_PREFIX),"unexpected remote temp path")
        public=bytes.fromhex(created["public"])
        need(len(public)==32,"remote signing trust key is invalid")
        approve=challenge(2)
        response=remote(payload,"challenge",path,approve)
        verify_receipt(response["receipt"].encode(),public_key=public,request=approve)
        need(response["pid"]!=created["pid"],"ledger was not reopened by new process")
        rejected=remote(payload,"reject-old-publish",path)
        need(rejected["rollback_denied"],"old publication was accepted")
        stale=challenge(1)
        old_response=remote(payload,"challenge",path,stale)
        highwater=json.loads(old_response["receipt"])["high_water_generation"]
        denied=False
        try:verify_receipt(old_response["receipt"].encode(),public_key=public,request=stale)
        except ValueError:denied=True
        need(denied and highwater==2,"durable higher generation failed to deny old")
        revoked=remote(payload,"revoke",path)
        need(revoked["revoked"],"revocation not persisted")
        post=remote(payload,"challenge",path,challenge(2))
        need(json.loads(post["receipt"])["revoked"] is True,
             "revocation did not survive a fresh remote process")
        result={"decision":"HOLD","witness_host":"Lenovo",
                "separate_remote_processes":True,
                "test_signer_key_temporarily_persisted_only_on_lenovo":True,
                "sqlite_generation_survived_process_restart":True,
                "old_generation_denied":True,
                "revocation_survived_process_restart":True,
                "witness_independently_administered":False,
                "whole_database_rollback_resistant":False,
                "production_modified":False,
                "model_or_provider_requests":0}
        return result
    finally:
        if path is not None:
            removed=remote(payload,"cleanup",path)
            need(removed["removed"],"remote ephemeral state cleanup failed")
        need(shell(production)==before,"production changed during canary")

if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--physical-disposable-lenovo",action="store_true")
    args=ap.parse_args()
    print(json.dumps(physical() if args.physical_disposable_lenovo else
          {"decision":"HOLD","physical_executed":False},sort_keys=True,indent=2))
