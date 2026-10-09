"""Fail-closed two-node SSH research client (NO local authority fallback).

Use only with the separately created disposable /tmp fixture on x1-370.
SSH failure/partition is an unavailable authority, not permission to copy
or rebootstrap its journal. No requests touch Neo4j or production services.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
import uuid

ROOT="/home/scott/git/.worktrees/assistx-two-host-authority-20261009"
CLI=ROOT+"/tests/probe_trace_two_host_owner_cli.py"
GRAPH="a"*64
DEST="x1-370"

# Validate only receipt structure and caller pins; verified SSH host identity
# remains necessary. This is NOT a signature, quorum or fencing proof.
def _trusted_decision(row: object, *, epoch: str, minimum_sequence: int) -> bool:
    if not isinstance(row,dict) or row.get("host")!=DEST:
        return False
    if row.get("epoch")!=epoch or row.get("graph_id")!=GRAPH:
        return False
    status=row.get("status")
    if status not in ("admitted","full","duplicate-query-ref",
                      "invalid-query-ref","authority-unavailable",
                      "authority-identity-uncertain"):
        return False
    if status in ("admitted","full","duplicate-query-ref"):
        seq=row.get("sequence")
        active=row.get("active")
        if (type(seq) is not int or seq<minimum_sequence
            or type(active) is not int or not 0<=active<=1):
            return False
    if status=="admitted":
        token=row.get("token")
        nonce=row.get("receiver_nonce")
        if (type(token) is not str or len(token)!=32
            or any(c not in "0123456789abcdef" for c in token)
            or active!=1 or seq<=0):
            return False
        try:
            parsed=uuid.UUID(nonce)
        except (ValueError,TypeError,AttributeError):
            return False
        return parsed.version==4 and str(parsed)==nonce
    return row.get("token") is None and row.get("receiver_nonce") is None


def run_one(query_ref:str, *, path:str, epoch:str,
            minimum_sequence:int,
            fail_transport:bool=False, owner_local:bool=False)->dict:
    if (os.environ.get("ASSISTX_TWOHOST_RESEARCH_ONLY")!="yes-disposable"
        or not path.startswith("/tmp/assistx-twohost-authority-test-")
        or not path.endswith("/authority-test.sqlite")
        or not query_ref.isascii()
        or not query_ref.startswith("synthetic-")
        or len(query_ref)>115
        or type(minimum_sequence) is not int or minimum_sequence<0):
        return {"status":"invalid-research-request"}
    args=[
        "env","ASSISTX_TWOHOST_RESEARCH_ONLY=yes-disposable",
        "PYTHONPATH="+ROOT+"/src",
        "python3",CLI,"admit",
        "--require-host",DEST,
        "--path",path,"--epoch",epoch,
        "--graph-id",GRAPH,"--minimum-sequence",str(minimum_sequence),
        "--query-ref",query_ref,
    ]
    host=socket.gethostname().split(".")[0]
    if owner_local:
        if host!=DEST:
            return {"status":"unsafe-local-owner"}
        command=args
    else:
        command=["ssh","-o","BatchMode=yes",
                 "-o","StrictHostKeyChecking=yes",
                 "-o","ConnectTimeout=3",
                 "-o","ConnectionAttempts=1"]
        if fail_transport:
            # Controlled connection failure, no changes to either host
            # network or firewall. This is NOT a real network partition.
            command.extend(["-o","ProxyCommand=/bin/false"])
        command.extend([DEST,*args])
    try:
        p=subprocess.run(command,capture_output=True,text=True,timeout=12)
        if p.returncode!=0:
            return {"status":"authority-unavailable",
                    "attempted_ssh":not owner_local}
        row=json.loads(p.stdout)
        if not _trusted_decision(row,epoch=epoch,minimum_sequence=minimum_sequence):
            return {"status":"untrusted-authority-response"}
        return {"status":row.get("status"),"origin":host,
                "sequence":row.get("sequence"),"active":row.get("active"),
                "attempted_ssh":not owner_local}
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return {"status":"authority-unavailable",
                "attempted_ssh":not owner_local}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--path",required=True)
    p.add_argument("--epoch",required=True)
    p.add_argument("--wave",type=int,choices=(1,3,5,10),required=True)
    # 0 is only a fresh-bootstrap checkpoint, NOT snapshot-rollback proof.
    p.add_argument("--minimum-sequence",type=int,required=True)
    p.add_argument("--owner-local",action="store_true")
    p.add_argument("--fail-transport",action="store_true")
    args=p.parse_args()
    host=socket.gethostname().split(".")[0]
    if host not in ("x1-370","xwing"):
        raise SystemExit("OUTSIDE_APPROVED_RESEARCH_HOST")
    if args.owner_local and args.fail_transport:
        raise SystemExit("CONFLICTING_TEST_MODES")
    def request(i):
        return run_one(f"synthetic-{host}-{args.wave}-{i}",
                       path=args.path,epoch=args.epoch,
                       minimum_sequence=args.minimum_sequence,
                       fail_transport=args.fail_transport,
                       owner_local=args.owner_local)
    # Cap SSH resource pressure at 3 concurrent requests per origin.
    with ThreadPoolExecutor(max_workers=min(args.wave,3)) as pool:
        responses=list(pool.map(request,range(args.wave)))
    summary={"origin":host,"wave_attempts":args.wave,
             "maximum_parallel_clients":min(args.wave,3),
             "admitted":sum(r["status"]=="admitted" for r in responses),
             "full":sum(r["status"]=="full" for r in responses),
             "unavailable":sum(r["status"]=="authority-unavailable" for r in responses),
             "other":sorted(set(r["status"] for r in responses
                 if r["status"] not in ("admitted","full","authority-unavailable"))),
             "transport_interrupted":args.fail_transport,
             "owner_local":args.owner_local}
    print(json.dumps(summary,sort_keys=True))
    if (summary["other"] or
        (args.fail_transport and (summary["admitted"] or summary["full"]))):
        raise SystemExit(3)

if __name__=="__main__":
    main()
