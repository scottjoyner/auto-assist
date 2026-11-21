#!/usr/bin/env python3
"""Physical *research-only* x1-370 -> Lenovo nonce-witness transport canary.

Temporary remote Python signer lives in memory, no remote files or services.
Same SSH administrator controls both hosts: NOT independent custody/high-water.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shlex
import socket
import subprocess
import tempfile
import threading
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from assistx import hermes_corpus_custody as corpus
from assistx import hermes_high_water as live

REMOTE_SIGNER = r"""
import json,sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
key=Ed25519PrivateKey.generate()
pub=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
sys.stdout.write(pub.hex()+"\n");sys.stdout.flush()
request=json.loads(sys.stdin.buffer.readline(4096))
assert set(request)=={"schema","nonce","owner","generation","checkpoint_sha256","manifest_sha256"}
assert request["schema"]==1 and len(request["nonce"])==64
reply={**request,"high_water_generation":2,"revoked":False}
payload=json.dumps(reply,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()
reply["signature"]=key.sign(payload).hex()
sys.stdout.write(json.dumps(reply,sort_keys=True,separators=(",",":"))+"\n")
sys.stdout.flush()
"""
EXPECTED_LIVE = "sha256:22191871cbf346ebe27c1b1cf433901f4b4ff479d80d2047917fe3966d78e307"

def need(ok, detail):
    if not ok:
        raise RuntimeError(detail)

def line_with_timeout(stream, timeout: float=7.0) -> bytes:
    ready,_,_=select.select([stream],[],[],timeout)
    need(bool(ready),"remote witness reply timeout")
    data=stream.readline(4098)
    need(0<len(data)<4098 and data.endswith(b"\n"),"remote witness framing")
    return data

def run_one(*, generation: int, location: Path):
    from assistx.hermes_corpus_checkpoint import verify_checkpoint
    loc = location / f"g{generation}"
    loc.mkdir(mode=0o700)
    ro,rw,trust = loc/"corpus",loc/"scratch",loc/"image"
    for x in (ro,rw,trust):
        x.mkdir(mode=0o700)
    raw_content=f"synthetic-generation-{generation}\n".encode()
    (ro/"synthetic.md").write_bytes(raw_content)
    corpus_sk=Ed25519PrivateKey.generate()
    corpus_pub=corpus_sk.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    checkpoint_sk=Ed25519PrivateKey.generate()
    checkpoint_pub=checkpoint_sk.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    need(corpus_pub!=checkpoint_pub,"signing roles must differ")
    (trust/"corpus.pub").write_bytes(corpus_pub)
    (trust/"checkpoint.pub").write_bytes(checkpoint_pub)
    manifest_payload={"schema":1,"root":str(ro),"entries":[
        {"path":"synthetic.md","sha256":hashlib.sha256(raw_content).hexdigest(),
         "size":len(raw_content)}]}
    signed={**manifest_payload,"signature":corpus_sk.sign(
        corpus.canonical(manifest_payload)).hex()}
    raw_manifest=json.dumps(signed,sort_keys=True).encode()
    (ro/corpus.MANIFEST_NAME).write_bytes(raw_manifest)
    witness_body={"schema":1,"generation":generation,
                  "root":str(ro),
                  "manifest_sha256":hashlib.sha256(raw_manifest).hexdigest(),
                  "corpus_key_sha256":hashlib.sha256(corpus_pub).hexdigest()}
    checkpoint={**witness_body,"signature":checkpoint_sk.sign(
        corpus.canonical(witness_body)).hex()}
    (trust/"checkpoint.json").write_text(json.dumps(checkpoint,sort_keys=True))
    del corpus_sk,checkpoint_sk
    command=("python3 -B -c "+shlex.quote(REMOTE_SIGNER))
    ssh=["ssh","-T","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes",
         "-o","ConnectTimeout=4","-o","ConnectionAttempts=1",
         "lenovo",command]
    process=subprocess.Popen(ssh,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
    server=None
    try:
        pub_hex=line_with_timeout(process.stdout).decode().strip()
        need(len(pub_hex)==64 and all(c in "0123456789abcdef" for c in pub_hex),
             "invalid remote public key")
        pub=bytes.fromhex(pub_hex)
        need(pub not in (corpus_pub,checkpoint_pub),"third signing key must differ")
        (trust/"remote-authority.pub").write_bytes(pub)
        sock=loc/"authority.sock"
        need(len(str(sock).encode()) < 104,"UNIX socket path too long")
        server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        server.settimeout(5)
        server.bind(str(sock))
        server.listen(1)
        observed={}
        def local_relay():
            try:
                conn,_=server.accept()
                with conn:
                    conn.settimeout(4)
                    req=b""
                    while not req.endswith(b"\n") and len(req)<2048:
                        part=conn.recv(1024)
                        if not part:break
                        req+=part
                    need(req.endswith(b"\n"),"client request incomplete")
                    process.stdin.write(req)
                    process.stdin.flush()
                    response=line_with_timeout(process.stdout,5)
                    conn.sendall(response)
                    observed["remote_signed"]=True
            except Exception as err:
                observed["error"]=type(err).__name__
        thread=threading.Thread(target=local_relay,daemon=True)
        thread.start()
        overrides={
            "HERMES_REQUIRE_EXTERNAL_HIGH_WATER":"true",
            "HERMES_HIGH_WATER_SOCKET":str(sock),
            "HERMES_HIGH_WATER_TRUST_KEY_PATH":str(trust/"remote-authority.pub"),
            "HERMES_HIGH_WATER_OWNER":"synthetic-hermes-test-owner",
        }
        old={k:os.environ.get(k) for k in overrides}
        try:
            os.environ.update(overrides)
            accepted=False
            try:
                result=verify_checkpoint(
                    read_root=str(ro),write_root=str(rw),
                    corpus_public_key=corpus_pub,signed_manifest=raw_manifest,
                    checkpoint_path=str(trust/"checkpoint.json"),
                    witness_key_path=str(trust/"checkpoint.pub"))
                accepted=(result==generation)
            except ValueError:
                accepted=False
        finally:
            for key,value in old.items():
                if value is None:os.environ.pop(key,None)
                else:os.environ[key]=value
        thread.join(timeout=6)
        need(not thread.is_alive() and observed.get("remote_signed") is True
             and not observed.get("error"),"separate Lenovo signer transport unsuccessful")
        need(process.wait(timeout=6)==0,"remote signer process nonzero")
        need(accepted == (generation==2),"higher retained high-water was not enforced")
        return {"generation":generation,
                "witness_high_water":2,"accepted":accepted,
                "remote_signer_process_exit":0}
    finally:
        if server is not None:server.close()
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
        for io in (process.stdin,process.stdout):
            if io is not None:io.close()

def physical():
    need(os.uname().nodename=="x1-370" and os.geteuid()==1000,"x1-370 UID1000 only")
    live=subprocess.run(["docker","inspect","-f","{{.Image}} {{.State.Running}}",
                        "assistx-hermes-adapter"],capture_output=True,text=True,timeout=8)
    need(live.returncode==0 and live.stdout.strip()==EXPECTED_LIVE+" true",
         "production Hermes identity changed")
    with tempfile.TemporaryDirectory(prefix="og-highwater-offhost-",
                                      dir="/home/scott/.cache") as tmp:
        result=[run_one(generation=2,location=Path(tmp)),
                run_one(generation=1,location=Path(tmp))]
    after=subprocess.run(["docker","inspect","-f","{{.Image}} {{.State.Running}}",
                          "assistx-hermes-adapter"],capture_output=True,text=True,timeout=8)
    need(after.returncode==0 and live.stdout==after.stdout,"production changed")
    return {"decision":"HOLD","witness_host":"Lenovo",
            "authority_independently_administered":False,
            "durable_high_water_verified":False,
            "production_changed":False,"provider_requests":0,
            "cases":result,"scratch_cleaned":True}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--physical-disposable-lenovo",action="store_true")
    args=p.parse_args()
    print(json.dumps(physical() if args.physical_disposable_lenovo else
                     {"decision":"HOLD","physical_executed":False},
                     sort_keys=True,indent=2))
