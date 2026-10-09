"""Synthetic xwing-backed pregrant witness and fail-closed x1 owner orchestration.

No route integration, no physical Neo4j dispatch, no automatic ledger repair,
and no failover promotion. x1-370 sends research-only SSH to xwing; if the
signed witness does not arrive, x1 does not call SingleAuthorityResearch.admit.
A passing response is an observation about the research two-step workflow,
not an end-to-end hardened remote executor.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from assistx.trace_two_host_witness_research import (
    Witness,bootstrap_disposable_witness,verify_external_grant,
)
from assistx.trace_two_host_authority_research import SingleAuthorityResearch

GRAPH="a"*64
BASE=Path("/home/scott/git/.worktrees/assistx-external-witness-fence-20261009")
WITNESS_CLI=BASE/"tests/probe_trace_witness_fence_cli.py"
RESEARCH_OPTIN="yes-disposable"
SHARED_EPOCH="1cf3d03a-6cbe-4320-831f-3ea1df290fb7"


def _require_lab_host(host):
    if host != socket.gethostname().split(".")[0]:
        raise ValueError("HOST_IDENTITY_MISMATCH")
    if os.environ.get("ASSISTX_TWOHOST_RESEARCH_ONLY") != RESEARCH_OPTIN:
        raise ValueError("RESEARCH_OPTIN_MISSING")


def _invoke_witness(witness_dir:str,*,epoch:str,query_ref:str,
                    pinned_pub:bytes,simulate_delivery_loss:bool=False)->dict:
    args=[
        "ssh","-o","BatchMode=yes",
        "-o","StrictHostKeyChecking=yes","-o","ConnectTimeout=4",
        "-o","ConnectionAttempts=1"
    ]
    if simulate_delivery_loss:
        args.extend(["-o","ProxyCommand=/bin/false"])
    args.extend(["xwing","env","ASSISTX_TWOHOST_RESEARCH_ONLY=yes-disposable",
                 "PYTHONPATH="+str(BASE/"src"),"python3",str(WITNESS_CLI),
                 "reserve","--require-host","xwing","--witness-dir",witness_dir,
                 "--epoch",epoch,"--public-hex",pinned_pub.hex(),
                 "--query-ref",query_ref])
    try:
        r=subprocess.run(args,capture_output=True,text=True,timeout=12)
        if r.returncode!=0:
            return {"status":"witness-unavailable"}
        row=json.loads(r.stdout)
        if not verify_external_grant(row,pinned_pub,epoch=epoch,graph_id=GRAPH,query_ref=query_ref):
            if row.get("status") in ("full","witness-unavailable"):
                return {"status":row["status"]}
            return {"status":"untrusted-witness-response"}
        return row
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return {"status":"witness-unavailable"}


def two_step_admit(*,primary_db:str,witness_dir:str,epoch:str,
                   pinned_public:bytes,query_ref:str,
                   simulate_delivery_loss:bool=False)->dict:
    """Only runs on exact x1-370 research host, no untrusted local promotion."""
    try:
        _require_lab_host("x1-370")
        if not (witness_dir.startswith("/tmp/assistx-twohost-witness-test-")
                and primary_db.startswith("/tmp/assistx-twohost-authority-test-")
                and primary_db.endswith("/authority-test.sqlite")
                and 1<=len(query_ref)<=128
                and query_ref.startswith("synthetic-")):
            raise ValueError("NOT_DISPOSABLE")
    except ValueError:
        return {"status":"invalid-research-request"}
    witnessed=_invoke_witness(witness_dir,epoch=epoch,query_ref=query_ref,
                              pinned_pub=pinned_public,
                              simulate_delivery_loss=simulate_delivery_loss)
    if witnessed.get("status")!="reserved":
        return {"status":witnessed.get("status","witness-unavailable"),
                "primary_called":False}
    try:
        authority=SingleAuthorityResearch(primary_db,pinned_epoch=epoch,
               pinned_graph_id=GRAPH,minimum_sequence=0)
        result=authority.admit(query_ref)
        return {
            "status":result.status,
            "primary_called":True,
            "witness_sequence":witnessed["grant"]["sequence"],
            "primary_sequence":result.sequence,
            "receiver_nonce_from_witness":witnessed["grant"]["receiver_nonce"],
            "witness_signature_verified":True,
            "physical_query_executed":False,
        }
    except (ValueError,OSError):
        # After witness reserve but before primary commit, the witness is
        # *left occupied*; no 'undo' endpoint exists. Safety > liveness.
        return {"status":"primary-unavailable","primary_called":True}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=("bootstrap","observe","reserve","two-step"))
    p.add_argument("--require-host",choices=("xwing","x1-370"),required=True)
    p.add_argument("--witness-dir",required=True)
    p.add_argument("--primary-db",default="")
    p.add_argument("--epoch",required=True)
    p.add_argument("--public-hex",default="")
    p.add_argument("--query-ref",default="")
    p.add_argument("--simulate-delivery-loss",action="store_true")
    a=p.parse_args()
    try:
        _require_lab_host(a.require_host)
        if a.action=="bootstrap":
            if a.require_host!="xwing":
                raise ValueError("NOT_WITNESS")
            pub=bootstrap_disposable_witness(a.witness_dir,epoch=a.epoch,graph_id=GRAPH)
            result={"status":"bootstrapped","public_hex":pub,"host":"xwing"}
        else:
            public=bytes.fromhex(a.public_hex)
            if a.action=="two-step":
                if a.require_host!="x1-370":
                    raise ValueError("NOT_PRIMARY")
                result=two_step_admit(primary_db=a.primary_db,witness_dir=a.witness_dir,
                   epoch=a.epoch,pinned_public=public,query_ref=a.query_ref,
                   simulate_delivery_loss=a.simulate_delivery_loss)
            else:
                if a.require_host!="xwing":
                    raise ValueError("NOT_WITNESS")
                w=Witness(a.witness_dir,epoch=a.epoch,graph_id=GRAPH,
                          expected_public_key=public)
                result=w.snapshot() if a.action=="observe" else w.reserve(a.query_ref)
                if a.action=="observe":
                    result={"status":"observed",**result}
            if a.require_host=="xwing":
                result["host"]="xwing"
        print(json.dumps(result,sort_keys=True))
    except (OSError,ValueError,Exception) as exc:
        # No secrets/paths in error report.
        print(json.dumps({"status":"witness-or-owner-unavailable",
                          "error_type":type(exc).__name__}))
        raise SystemExit(4)


if __name__=="__main__":
    main()
