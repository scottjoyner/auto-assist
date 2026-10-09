"""Actual two-host SSH research roundtrip: x1 reserve -> xwing veto -> verify.

Even a successfully signed two-host proposal is NEVER permission to execute a
Neo4j query. Missing xwing/wrong signature leaves x1's slot occupied.
No failover, rollback rearm, production route, physical query, or slot release.
Only disposable test files. Pre-enrolled key file is read BEFORE SSH request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess

from assistx.trace_two_host_authority_research import SingleAuthorityResearch
from assistx.trace_two_host_veto_witness_research import verify_pretrusted_endorsement


ROOT="/home/scott/git/.worktrees/assistx-two-host-veto-witness-20261009"
WITNESS_CLI=ROOT+"/tests/probe_trace_two_host_veto_witness_cli.py"
GRAPH="a"*64


def run(*,authority_path,epoch,witness_folder,pinned_key_path,
        approved_sha256,query_ref,cut_off_witness=False):
    if (socket.gethostname().split(".")[0]!="x1-370"
        or os.getenv("ASSISTX_TWOHOST_RESEARCH_ONLY")!="yes-disposable"
        or not authority_path.startswith("/tmp/assistx-twohost-authority-test-")
        or not witness_folder.startswith("/tmp/assistx-twohost-witness-test-")
        or pinned_key_path!="/tmp/assistx-twohost-veto-pin-20261009-physical.pub"
        or not query_ref.startswith("synthetic-")):
        raise ValueError("DISPOSABLE_PHYSICAL_RESEARCH_ONLY")
    pub_file=Path(pinned_key_path)
    st=pub_file.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_mode&0o077 or st.st_nlink!=1):
        raise ValueError("UNSAFE_PRE_ENROLLED_PUBLIC_KEY")
    trusted=pub_file.read_bytes()
    if (len(trusted)!=32
        or hashlib.sha256(trusted).hexdigest()!=approved_sha256):
        raise ValueError("PRE_ENROLLED_WITNESS_KEY_MISMATCH")

    primary=SingleAuthorityResearch(
        authority_path,pinned_epoch=epoch,pinned_graph_id=GRAPH,
        minimum_sequence=0)
    d=primary.admit(query_ref)
    if d.status!="admitted":
        return {"status":d.status,"origin":"x1-370",
                "execution_authorized":False,"active":primary.snapshot()["active"]}
    cmd=[
        "ssh","-o","BatchMode=yes",
        "-o","StrictHostKeyChecking=yes",
        "-o","ConnectTimeout=4",
        "-o","ConnectionAttempts=1",
    ]
    if cut_off_witness:
        cmd+=["-o","ProxyCommand=/bin/false"]
    cmd+=["xwing","env","ASSISTX_TWOHOST_RESEARCH_ONLY=yes-disposable",
          "PYTHONPATH="+ROOT+"/src","python3",WITNESS_CLI,"endorse",
          "--folder",witness_folder,"--epoch",epoch,"--graph-id",GRAPH,
          "--signer-digest",approved_sha256,
          "--minimum-sequence","0",
          "--token",d.token,"--nonce",d.receiver_nonce,
          "--query-ref",query_ref,"--sequence",str(d.sequence)]
    accepted=False
    try:
        response=subprocess.run(cmd,capture_output=True,text=True,timeout=16)
        reply=json.loads(response.stdout) if response.returncode==0 else {}
        if reply.pop("host",None)=="xwing":
            accepted=verify_pretrusted_endorsement(
                reply,public_key=trusted,approved_sha256=approved_sha256,
                epoch=epoch,graph_id=GRAPH,token=d.token,
                receiver_nonce=d.receiver_nonce,query_ref=query_ref,
                sequence=d.sequence)
    except (OSError,ValueError,subprocess.TimeoutExpired):
        pass

    active=primary.snapshot()["active"]
    if active!=1:
        raise RuntimeError("PRIMARY_OCCUPANCY_UNEXPECTED")
    return {
        "status":"proposal-dual-witnessed-research-only" if accepted else
                 "witness-unavailable-conservative-hold",
        "origin":"x1-370","witness_host":"xwing",
        "sequence":d.sequence,"active":active,
        "witness_signature_valid":accepted,
        "execution_authorized":False,
        "slot_released":False,
    }


if __name__=="__main__":
    a=argparse.ArgumentParser()
    a.add_argument("--authority-path",required=True)
    a.add_argument("--epoch",required=True)
    a.add_argument("--witness-folder",required=True)
    a.add_argument("--pinned-key-path",required=True)
    a.add_argument("--approved-sha256",required=True)
    a.add_argument("--query-ref",required=True)
    a.add_argument("--cut-off-witness",action="store_true")
    x=a.parse_args()
    print(json.dumps(run(**vars(x)),sort_keys=True))
