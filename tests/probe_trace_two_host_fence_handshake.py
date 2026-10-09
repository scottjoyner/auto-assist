#!/usr/bin/env python3
"""x1-370 research 2-of-2 admission preparation, NO physical query dispatch.

A local owner reservation alone is NEVER an executable admission. After a
durable owner commit it asks a separately pinned xwing witness to record the
exact epoch/graph/term/sequence/token/nonce/query. If the SSH hop fails, the
owner remains occupied forever and only a denial is returned. No fallback.
"""
from __future__ import annotations
import argparse
import json
import os
import socket
import subprocess

from assistx.trace_two_host_authority_research import SingleAuthorityResearch

ROOT="/home/scott/git/.worktrees/assistx-two-host-fence-handshake-20261009"
WITNESS_CLI=ROOT+"/tests/probe_trace_two_host_fence_witness_cli.py"


def prepare(*, owner_path, witness_path, epoch, graph, term, query_ref,
            fail_transport=False):
    if (socket.gethostname().split(".")[0]!="x1-370"
        or os.environ.get("ASSISTX_TWOHOST_RESEARCH_ONLY")!="yes-disposable"
        or type(query_ref) is not str or not query_ref.startswith("synthetic-")):
        return {"status":"invalid-or-untrusted-research-origin"}
    try:
        owner=SingleAuthorityResearch(owner_path,pinned_epoch=epoch,
                                      pinned_graph_id=graph,minimum_sequence=0)
        decision=owner.admit(query_ref)
    except Exception:
        return {"status":"authority-unavailable"}
    if decision.status!="admitted":
        return {"status":decision.status,"physical_dispatch_permitted":False}
    # The owner slot is now held. An unreachable or stale witness never
    # leads to a local approval, reset, retry-on-copy, or auto-expiration.
    args=["ssh","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes",
          "-o","ConnectTimeout=3","-o","ConnectionAttempts=1"]
    if fail_transport:
        args+=["-o","ProxyCommand=/bin/false"]
    args+=["xwing","env","ASSISTX_TWOHOST_RESEARCH_ONLY=yes-disposable",
           "PYTHONPATH="+ROOT+"/src","python3",WITNESS_CLI,"attest",
           "--path",witness_path,"--epoch",epoch,"--graph-id",graph,
           "--term",str(term),"--sequence",str(decision.sequence),
           "--token",decision.token,"--receiver-nonce",decision.receiver_nonce,
           "--query-ref",query_ref]
    try:
        reply=subprocess.run(args,capture_output=True,text=True,timeout=12)
        if reply.returncode!=0:
            return {"status":"held-witness-unavailable","physical_dispatch_permitted":False}
        response=json.loads(reply.stdout)
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return {"status":"held-witness-unavailable","physical_dispatch_permitted":False}
    if (type(response) is not dict or response.get("status")!="witnessed-no-physical-dispatch"
        or response.get("host")!="xwing" or response.get("sequence")!=decision.sequence
        or response.get("term")!=term or response.get("token")!=decision.token
        or response.get("receiver_nonce")!=decision.receiver_nonce
        or response.get("query_ref")!=query_ref):
        return {"status":"held-witness-not-accepted","physical_dispatch_permitted":False}
    # Research only: this confirms TWO durable reservations with independent
    # origin paths, not physical query authority, production RBAC or fencing.
    return {"status":"two-of-two-research-witnessed",
            "sequence":decision.sequence,"term":term,
            "physical_dispatch_permitted":False}


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--owner-path",required=True)
    p.add_argument("--witness-path",required=True)
    p.add_argument("--epoch",required=True)
    p.add_argument("--graph-id",required=True)
    p.add_argument("--term",type=int,required=True)
    p.add_argument("--query-ref",required=True)
    p.add_argument("--fail-transport",action="store_true")
    args=p.parse_args()
    result=prepare(owner_path=args.owner_path,witness_path=args.witness_path,
         epoch=args.epoch,graph=args.graph_id,term=args.term,
         query_ref=args.query_ref,fail_transport=args.fail_transport)
    print(json.dumps(result,sort_keys=True))
