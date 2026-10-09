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

ROOT="/home/scott/git/.worktrees/assistx-two-host-authority-20261009"
CLI=ROOT+"/tests/probe_trace_two_host_owner_cli.py"
GRAPH="a"*64
DEST="x1-370"


def run_one(query_ref:str, *, path:str, epoch:str,
            fail_transport:bool=False, owner_local:bool=False)->dict:
    if (os.environ.get("ASSISTX_TWOHOST_RESEARCH_ONLY")!="yes-disposable"
        or not path.startswith("/tmp/assistx-twohost-authority-test-")
        or not path.endswith("/authority-test.sqlite")
        or not query_ref.isascii()
        or not query_ref.startswith("synthetic-")
        or len(query_ref)>115):
        return {"status":"invalid-research-request"}
    args=[
        "env","ASSISTX_TWOHOST_RESEARCH_ONLY=yes-disposable",
        "PYTHONPATH="+ROOT+"/src",
        "python3",CLI,"admit",
        "--require-host",DEST,
        "--path",path,"--epoch",epoch,
        "--graph-id",GRAPH,"--minimum-sequence","0",
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
        if not isinstance(row,dict) or row.get("host")!=DEST:
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
