#!/usr/bin/env python3
"""#148 three-host source-owned SSH client, no copied-ledger fallback.

Call only from x1-370/xwing, to the exact disposable Raspberry Pi witness
over preexisting SSH with host key verification. No network listener.
Not a distributed consensus admission authority.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import uuid
import socket
import subprocess

TARGET="raspberrypi"
PI_SCRIPT="/tmp/assistx-threehost-witness-test-20261009/probe_trace_third_node_witness.py"
PI_DB="/tmp/assistx-threehost-witness-test-20261009/witness-test.sqlite"
GRAPH="a"*64


def submit(query_ref:str,epoch:str,*,fail_ssh=False)->dict:
    origin=socket.gethostname().split(".")[0]
    try:
        valid_epoch=(type(epoch) is str and str(uuid.UUID(epoch))==epoch
                     and uuid.UUID(epoch).version==4)
    except (TypeError,ValueError,AttributeError):
        valid_epoch=False
    if (origin not in ("x1-370","xwing")
        or os.getenv("ASSISTX_THREEHOST_RESEARCH_ONLY")!="yes-disposable"
        or type(query_ref) is not str
        or re.fullmatch(r"synthetic-[A-Za-z0-9_.:-]{1,110}",query_ref) is None
        or not valid_epoch):
        return {"status":"invalid-research-request","origin":origin}
    cmd=["ssh","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes",
         "-o","ConnectTimeout=3","-o","ConnectionAttempts=1"]
    if fail_ssh:
        # Break only THIS process's connection; do not alter Tailscale,
        # firewall, SSH agents, or target host availability.
        cmd+=["-o","ProxyCommand=/bin/false"]
    cmd += [
        TARGET,"env","ASSISTX_THREEHOST_RESEARCH_ONLY=yes-disposable",
        "python3",PI_SCRIPT,"request","--path",PI_DB,
        "--epoch",epoch,"--graph-id",GRAPH,"--query-ref",query_ref,
    ]
    try:
        completed=subprocess.run(cmd,capture_output=True,text=True,timeout=13)
        if completed.returncode:
            return {"status":"authority-unavailable","origin":origin}
        obj=json.loads(completed.stdout)
        if type(obj) is not dict or obj.get("host")!=TARGET or obj.get("status") not in (
            "admitted","full","authority-unavailable","invalid-query-ref"):
            return {"status":"untrusted-authority-response","origin":origin}
        if obj["status"] in ("admitted","full"):
            if (obj.get("query_ref")!=query_ref
                or obj.get("epoch")!=epoch or obj.get("graph_id")!=GRAPH
                or type(obj.get("sequence")) is not int
                or obj["sequence"]<1 or obj.get("active")!=1):
                return {"status":"untrusted-authority-response","origin":origin}
            if obj["status"]=="admitted":
                try:
                    nonce_valid=(type(obj.get("receiver_nonce")) is str
                        and uuid.UUID(obj["receiver_nonce"]).version==4
                        and str(uuid.UUID(obj["receiver_nonce"]))==obj["receiver_nonce"])
                except (TypeError,ValueError,AttributeError):
                    nonce_valid=False
                token=obj.get("token")
                if (type(token) is not str or re.fullmatch("[0-9a-f]{32}",token) is None
                    or not nonce_valid):
                    return {"status":"untrusted-authority-response","origin":origin}
        return {"status":obj["status"],"origin":origin,
                "sequence":obj.get("sequence"),"active":obj.get("active")}
    except (OSError,subprocess.TimeoutExpired,ValueError):
        return {"status":"authority-unavailable","origin":origin}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--epoch",required=True)
    p.add_argument("--wave",type=int,choices=(1,3,5,10),required=True)
    p.add_argument("--fail-ssh",action="store_true")
    x=p.parse_args()
    origin=socket.gethostname().split(".")[0]
    if origin not in ("x1-370","xwing"):
        raise SystemExit("HOST_OUTSIDE_APPROVED_RESEARCH")
    with ThreadPoolExecutor(max_workers=min(x.wave,3)) as pool:
        replies=list(pool.map(lambda i:submit(
            f"synthetic-{origin}-{x.wave}-{i}",x.epoch,fail_ssh=x.fail_ssh
        ),range(x.wave)))
    report={"origin":origin,"requests":x.wave,"max_parallel":min(x.wave,3),
            "admitted":sum(r["status"]=="admitted" for r in replies),
            "denied_full":sum(r["status"]=="full" for r in replies),
            "unavailable":sum(r["status"]=="authority-unavailable" for r in replies),
            "other":[r["status"] for r in replies if r["status"] not in (
                "admitted","full","authority-unavailable")],
            "ssh_deliberately_failed":x.fail_ssh}
    print(json.dumps(report,sort_keys=True))
    if report["other"] or (x.fail_ssh and (report["admitted"] or report["denied_full"])):
        raise SystemExit(4)


if __name__=="__main__":main()
