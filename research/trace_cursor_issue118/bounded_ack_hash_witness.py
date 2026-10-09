#!/usr/bin/env python3
"""Single bounded read-only legacy ACK content witness. Never repairs custody.

Safeguards: pinned local source, fresh L1 GO, expected ext4/Btrfs identity,
<=8 archives, <=24 MiB total, low-priority NAS read and aggregate-only output.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys

ROOT=Path("/media/scott/SSD_4TB/finetune-trace-spool/sealed")
ADMISSION=Path("/media/scott/SSD_4TB/knowledge/10-Infrastructure/storage/nas-io-admission.sh")
OUT=Path("/home/scott/git/delm-sandbox/trace-drainer-review-20261009")
MAX_FILES=8
MAX_BYTES=24*1024*1024
NAME=re.compile(r"trc-[A-Za-z0-9_-]+-\d{8}T\d{6}Z-[a-f0-9]{8}\.tar\.zst\Z")
HEX=re.compile(r"[a-f0-9]{64}\Z")

def sha(data):return hashlib.sha256(data).hexdigest()

def read_json_small(path,cap=8192):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>cap:
        raise ValueError("unsafe local ACK")
    with path.open("rb") as f:data=f.read(cap+1)
    if len(data)>cap:raise ValueError("oversized local ACK")
    return json.loads(data)

def pin_hash(path,expected_size,expected_sha):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        a=os.fstat(fd)
        if not stat.S_ISREG(a.st_mode) or a.st_size!=expected_size:
            raise ValueError("source generation/size changed")
        h=hashlib.sha256()
        while True:
            block=os.read(fd,256*1024)
            if not block:break
            h.update(block)
        b=os.fstat(fd)
        if (a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns)!=(
            b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns,b.st_ctime_ns):
            raise ValueError("source changed during SHA witness")
        if h.hexdigest()!=expected_sha:raise ValueError("source archive digest mismatch")
    finally:os.close(fd)

def research_headroom_ok(props):
    """Additional conservative read budget; never weakens L1 GO policy."""
    try:
        util=float(props["util_pct"])
        iowait=float(props["iowait_pct"])
        return (0 <= util < 70 and 0 <= iowait < 20)
    except (KeyError,ValueError,TypeError):
        return False


def preflight_go():
    fs=subprocess.run(["findmnt","-T",str(ROOT),"-n","-o","SOURCE,FSTYPE"],
                      capture_output=True,text=True,timeout=6)
    if fs.returncode or fs.stdout.strip().split()!=["/dev/nvme1n1p1","ext4"]:
        raise RuntimeError("source volume identity unproven")
    p=subprocess.run(["timeout","17","bash",str(ADMISSION),"--lane","L1"],
        capture_output=True,text=True,timeout=20,check=False)
    props={}
    for line in p.stdout.splitlines():
        key,sep,value=line.partition("=")
        if sep:props[key]=value.strip()
    if (p.returncode!=0 or props.get("decision")!="GO" or
        props.get("lane")!="L1" or props.get("btrfs_errors")!="0"):
        raise RuntimeError("NO_L1_GO: fail closed before archive reads")
    if not research_headroom_ok(props):
        raise RuntimeError("RESEARCH_HEADROOM_HOLD: protect NAS5 recovery")
    return {"util_pct":props.get("util_pct"),"iowait_pct":props.get("iowait_pct")}

REMOTE_CODE=r'''
import hashlib,json,os,pathlib,re,stat,subprocess,sys
root=pathlib.Path("/nas/fileserver/auto-fintune/traces/inbox")
mount=subprocess.run(["findmnt","-T",str(root),"-n","-o","SOURCE,FSTYPE,UUID"],
    capture_output=True,text=True,timeout=6)
if mount.returncode or mount.stdout.strip().split()!=[
  "/dev/sdd2","btrfs","0880694b-51c8-42be-b8d2-c8ac119f2b58"]:
    raise SystemExit("BACKEND_IDENTITY_HOLD")
rows=json.load(sys.stdin)
if not isinstance(rows,list) or not 1<=len(rows)<=8:raise SystemExit("BATCH_INVALID")
if sum(x.get("size",0) for x in rows)>24*1024*1024:raise SystemExit("BYTE_CAP")
name=re.compile(r"trc-[A-Za-z0-9_-]+-\d{8}T\d{6}Z-[a-f0-9]{8}\.tar\.zst\Z")
results=[]
for x in rows:
  n=x["name"]
  if type(n) is not str or not name.fullmatch(n):raise SystemExit("NAME_INVALID")
  if not isinstance(x["size"],int) or x["size"]<0:raise SystemExit("SIZE_INVALID")
  if not re.fullmatch(r"[a-f0-9]{64}",x["sha"]):raise SystemExit("DIGEST_INVALID")
  p=root/n
  fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
  try:
    a=os.fstat(fd)
    if not stat.S_ISREG(a.st_mode) or a.st_size!=x["size"]:raise SystemExit("REMOTE_STAT_MISMATCH")
    h=hashlib.sha256()
    while True:
      block=os.read(fd,256*1024)
      if not block:break
      h.update(block)
    b=os.fstat(fd)
    stable=(a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns)==(
      b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns,b.st_ctime_ns)
    results.append({"id":hashlib.sha256(n.encode()).hexdigest(),
                    "digest_match":h.hexdigest()==x["sha"],"stable":stable})
  finally:os.close(fd)
print(json.dumps(results,sort_keys=True))
'''



JOURNAL=OUT/"legacy-ack-private-content-witness.jsonl"

def verified_prior(fd):
    """Retain previous per-source attestations privately; reject malformed state."""
    os.lseek(fd,0,os.SEEK_SET)
    known={}
    with os.fdopen(os.dup(fd),"r",encoding="utf-8") as reader:
        for line in reader:
            if not line.strip():continue
            row=json.loads(line)
            if (row.get("schema")!="trace-ack-content-witness/v1" or
                not isinstance(row.get("entries"),list)):
                raise RuntimeError("untrusted private journal")
            for item in row["entries"]:
                identity=item.get("id")
                digest=item.get("sha256")
                size=item.get("bytes")
                if (not isinstance(identity,str) or not HEX.fullmatch(identity)
                    or not isinstance(digest,str) or not HEX.fullmatch(digest)
                    or type(size) is not int or size<0):
                    raise RuntimeError("invalid attestation row")
                record=(digest,size)
                if identity in known and known[identity]!=record:
                    raise RuntimeError("conflicting prior witness")
                known[identity]=record
    return known

def select_batch(entries,prior):
    """Deterministically spread one bounded batch across unverified entries."""
    pending=[]
    for x in entries:
        identity=sha(x["name"].encode())
        if identity in prior:
            if prior[identity]!=(x["sha"],x["size"]):
                raise RuntimeError("ACK mutated since prior independent hash")
            continue
        pending.append(x)
    if not pending:return []
    chosen=[]
    candidates=[pending[(len(pending)*(2*i+1))//(2*min(MAX_FILES,len(pending)))]
                for i in range(min(MAX_FILES,len(pending)))]
    for x in candidates:
        if x["name"] in {y["name"] for y in chosen}:continue
        if x["size"]+sum(y["size"] for y in chosen)>MAX_BYTES:continue
        chosen.append(x)
    if not chosen:
        raise RuntimeError("no safe bounded batch for remaining ACKs")
    return chosen

def record_batch(fd,selected):
    receipts=[{"id":sha(x["name"].encode()),"sha256":x["sha"],"bytes":x["size"]}
              for x in selected]
    blob=json.dumps({"schema":"trace-ack-content-witness/v1","entries":receipts},
                    sort_keys=True,separators=(",",":"))+"\n"
    os.lseek(fd,0,os.SEEK_END)
    os.write(fd,blob.encode())
    os.fsync(fd)

def main():
    pre=preflight_go()
    entries=[]
    for ack in sorted(ROOT.glob("*.tar.zst.ack.json")):
        name=ack.name.removesuffix(".ack.json")
        if not NAME.fullmatch(name):continue
        obj=read_json_small(ack)
        if (obj.get("schema")!="safe-trace-nas-ack/v1" or
            obj.get("source_release")!="BLOCKED" or
            obj.get("beelink_uuid")!="0880694b-51c8-42be-b8d2-c8ac119f2b58"):
            raise RuntimeError("legacy ACK not custody-safe")
        nbytes=obj.get("bytes")
        digest=obj.get("sha256")
        if type(nbytes) is not int or nbytes<0 or not isinstance(digest,str) or not HEX.fullmatch(digest):
            raise RuntimeError("invalid ACK archive digest or size")
        if obj.get("nas_remote_path")!="/nas/auto-fintune/traces/inbox/"+name:
            raise RuntimeError("ACK destination mismatch")
        entries.append({"name":name,"size":nbytes,"sha":digest})
    if not entries:raise RuntimeError("no safely verified ACKs")
    with open(JOURNAL,"a+",encoding="utf-8") as ledger:
        os.chmod(JOURNAL,0o600)
        fcntl.flock(ledger.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        prior=verified_prior(ledger.fileno())
        selected=select_batch(entries,prior)
        if not selected:
            print(json.dumps({"verdict":"ALL_PREVIOUSLY_WITNESSED",
                              "private_witness_count":len(prior),
                              "migration_authorized":False}))
            return
        for x in selected:pin_hash(ROOT/x["name"],x["size"],x["sha"])
        verify_selected(entries,selected,pre,prior,ledger.fileno())


def verify_selected(entries,selected,pre,prior,journal_fd):
    request=json.dumps(selected)
    cmd="ionice -c3 nice -n 19 python3 -c "+shlex.quote(REMOTE_CODE)
    result=subprocess.run(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=5",
                           "root@192.168.1.202",cmd],input=request,capture_output=True,
                          text=True,timeout=25,check=False)
    if result.returncode:raise RuntimeError("remote hash read failed, fail closed")
    remote=json.loads(result.stdout)
    byid={sha(x["name"].encode()):x for x in selected}
    if len(remote)!=len(selected) or {x.get("id") for x in remote}!=set(byid):
        raise RuntimeError("inconsistent hash responses")
    for x in remote:
        if x.get("digest_match") is not True or x.get("stable") is not True:
            raise RuntimeError("remote content mismatch or changed during hash")
    record_batch(journal_fd,selected)
    report={"scope":"single low-priority verified read-only NAS hash batch",
        "l1_precheck":pre,"local_ack_count_at_scan":len(entries),
        "archives_checked":len(selected),"archive_bytes_checked_per_host":sum(x["size"] for x in selected),
        "private_cumulative_witness_count":len(prior)+len(selected),
        "local_sha256_verified":len(selected),
        "remote_sha256_verified":len(remote),
        "remote_backend_uuid_verified":True,
        "no_remote_ready_repairs":True,"nas_writes":0,
        "not_entire_ack_set_verified":True,
        "migration_or_source_release_authorized":False}
    fd=os.open(OUT/"legacy-ack-bounded-hash-batch-20261009.json",os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,"w") as file:json.dump(report,file,indent=2)
    print(json.dumps(report,sort_keys=True))

if __name__=="__main__":
    main()

