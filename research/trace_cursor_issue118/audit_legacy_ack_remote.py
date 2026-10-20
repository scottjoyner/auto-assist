#!/usr/bin/env python3
"""Issue #118: read-only legacy ACK custody census.

No archive payload reads, NAS writes, receipt repair, source removal or ACK edits.
Local sidecars hashed; remote sidecars checked through Beelink's local filesystem.
"""
import hashlib
import json
import pathlib
import re
import shlex
import stat
import subprocess
from collections import Counter

ROOT=pathlib.Path("/media/scott/SSD_4TB/finetune-trace-spool/sealed")
LOCAL_PREFIX="/nas/auto-fintune/traces/inbox/"
HOST="root@192.168.1.202"
EXPECTED_UUID="0880694b-51c8-42be-b8d2-c8ac119f2b58"
NAME=re.compile(r"trc-[A-Za-z0-9_-]+-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.tar\.zst\Z")
HEX=re.compile(r"[a-f0-9]{64}\Z")


def read_small_regular(path,max_bytes):
    st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_size>max_bytes:
        raise ValueError("unsafe or oversized local sidecar")
    with path.open("rb") as stream:
        blob=stream.read(max_bytes+1)
    if len(blob)>max_bytes:
        raise ValueError("oversized sidecar")
    return blob


def local_census(root):
    counts=Counter()
    remote=[]
    for ack in sorted(root.glob("*.tar.zst.ack.json")):
        counts["ack_entries"]+=1
        try:
            n=ack.name.removesuffix(".ack.json")
            if not NAME.fullmatch(n):
                raise ValueError("invalid archive name")
            obj=json.loads(read_small_regular(ack,8192))
            if not isinstance(obj,dict) or obj.get("schema")!="safe-trace-nas-ack/v1":
                raise ValueError("invalid ack schema")
            if obj.get("beelink_uuid")!=EXPECTED_UUID or obj.get("source_release")!="BLOCKED":
                raise ValueError("wrong destination or source release")
            if obj.get("nas_remote_path")!=LOCAL_PREFIX+n:
                raise ValueError("unexpected remote destination")
            wanted=obj.get("sha256")
            if not isinstance(wanted,str) or not HEX.fullmatch(wanted):
                raise ValueError("malformed expected archive digest")
            src=root/n
            st=src.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_size!=obj.get("bytes"):
                raise ValueError("source stat differs from ACK")
            manifest=read_small_regular(root/(n+".manifest.json"),4*1024*1024)
            ready=read_small_regular(root/(n+".ready"),128)
            if hashlib.sha256(manifest).hexdigest()!=obj.get("manifest_sha256"):
                raise ValueError("local manifest digest mismatch")
            if ready!=wanted.encode()+b"\n":
                raise ValueError("local ready does not match ACK archive digest")
            counts["local_provenance_structurally_valid"]+=1
            counts["legacy_ack_missing_ready_sha256" if "ready_sha256" not in obj else "ready_sha256_present"]+=1
            remote.append({"n":n,"archive_size":st.st_size,
                           "manifest_sha":hashlib.sha256(manifest).hexdigest(),
                           "ready_sha":hashlib.sha256(ready).hexdigest()})
        except (OSError,ValueError,TypeError,KeyError):
            counts["local_invalid_or_untrusted"]+=1
    return counts,remote


REMOTE_CODE=r'''
import hashlib,json,os,pathlib,re,stat,subprocess,sys
from collections import Counter
root=pathlib.Path("/nas/fileserver/auto-fintune/traces/inbox")
if not root.is_dir() or root.is_symlink():raise SystemExit("REMOTE_ROOT_UNSAFE")
mount=subprocess.run(["findmnt","-T",str(root),"-n","-o","SOURCE,FSTYPE,UUID"],
                     capture_output=True,text=True,timeout=8,check=False)
if mount.returncode or mount.stdout.strip().split()!=[
    "/dev/sdd2","btrfs","0880694b-51c8-42be-b8d2-c8ac119f2b58"]:
    raise SystemExit("REMOTE_BACKEND_IDENTITY_UNPROVEN")
names=re.compile(r"trc-[A-Za-z0-9_-]+-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.tar\.zst\Z")
inputs=json.load(sys.stdin)
if type(inputs) is not list or len(inputs)>512:raise SystemExit("BAD_INPUT")
c=Counter()
for item in inputs:
  n=item["n"]
  if type(n) is not str or "/" in n or not names.fullmatch(n):
    c["unsafe_input"]+=1;continue
  c["checked"]+=1
  for suffix in ("",".manifest.json",".ready"):
    p=root/(n+suffix)
    label={"":"archive",".manifest.json":"manifest",".ready":"ready"}[suffix]
    try:
      st=p.lstat()
      if not stat.S_ISREG(st.st_mode):
        c[label+"_nonregular"]+=1;continue
      c[label+"_present"]+=1
      if suffix=="":
        c["archive_stat_size_match" if st.st_size==item["archive_size"] else "archive_stat_size_mismatch"]+=1
      elif st.st_size>4194304:
        c[label+"_oversized"]+=1
      else:
        with p.open("rb") as f:raw=f.read(4194305)
        expected=item["manifest_sha" if suffix==".manifest.json" else "ready_sha"]
        c[label+"_digest_match" if hashlib.sha256(raw).hexdigest()==expected else label+"_digest_mismatch"]+=1
    except FileNotFoundError:
      c[label+"_absent"]+=1
    except OSError:
      c[label+"_stat_or_read_error"]+=1
print(json.dumps(dict(c),sort_keys=True))
'''


def remote_check(records):
    if not records:return {}
    command="python3 -c "+shlex.quote(REMOTE_CODE)
    p=subprocess.run(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=5",HOST,command],
       input=json.dumps(records),text=True,capture_output=True,timeout=30,check=False)
    if p.returncode:
        raise RuntimeError("remote read-only audit failed "+str(p.returncode))
    return json.loads(p.stdout)


def main():
    local,records=local_census(ROOT)
    result={"local":dict(local),"remote":remote_check(records),
            "source_payload_bytes_read":0,"remote_archive_payload_bytes_read":0,
            "nas_write_ops":0,"ack_or_source_changes":0,
            "complete_custody_attestation":False,
            "production_migration_authorized":False}
    print(json.dumps(result,indent=2,sort_keys=True))


if __name__=="__main__":
    main()

