#!/usr/bin/env python3
"""Independent xwing witness CLI; research data under /tmp only.

Invocations happen via preexisting SSH, no daemon/listener, graph or provider.
Research opt-in and exact xwing hostname are mandatory. Outputs do not contain
secrets; the token is only an inert synthetic admission research marker.
"""
from __future__ import annotations
import argparse
import json
import os
import socket

from assistx.trace_two_host_fence_research import (
    IndependentResearchWitness,bootstrap_witness,
)


def main():
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=("bootstrap","attest","observe"))
    p.add_argument("--path",required=True)
    p.add_argument("--epoch",required=True)
    p.add_argument("--graph-id",required=True)
    p.add_argument("--term",required=True,type=int)
    p.add_argument("--min-sequence",default=0,type=int)
    p.add_argument("--sequence",default=0,type=int)
    p.add_argument("--token",default="")
    p.add_argument("--receiver-nonce",default="")
    p.add_argument("--query-ref",default="")
    a=p.parse_args()
    if (socket.gethostname().split(".")[0]!="xwing"
        or os.environ.get("ASSISTX_TWOHOST_RESEARCH_ONLY")!="yes-disposable"):
        raise SystemExit("WITNESS_HOST_AND_OPT_IN_REQUIRED")
    try:
        if a.action=="bootstrap":
            if a.sequence or a.min_sequence or a.token or a.receiver_nonce or a.query_ref:
                raise ValueError("INVALID_BOOTSTRAP")
            bootstrap_witness(a.path,epoch=a.epoch,graph_id=a.graph_id,term=a.term)
            result={"status":"bootstrapped","host":"xwing"}
        else:
            witness=IndependentResearchWitness(
                a.path, expected_epoch=a.epoch,expected_graph=a.graph_id,
                expected_term=a.term,independent_min_sequence=a.min_sequence)
            if a.action=="observe":
                result={"status":"observed","host":"xwing",**witness.observe()}
            else:
                decision=witness.attest(
                    epoch=a.epoch,graph_id=a.graph_id,term=a.term,
                    sequence=a.sequence,token=a.token,
                    receiver_nonce=a.receiver_nonce,query_ref=a.query_ref)
                result={"status":decision.status,"host":"xwing",
                        "sequence":decision.sequence,"term":decision.term,
                        "token":a.token if decision.status=="witnessed-no-physical-dispatch" else None,
                        "receiver_nonce":a.receiver_nonce if decision.status=="witnessed-no-physical-dispatch" else None,
                        "query_ref":a.query_ref if decision.status=="witnessed-no-physical-dispatch" else None}
        print(json.dumps(result,sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status":"witness-unavailable","host":"xwing",
                          "error_class":type(exc).__name__}),flush=True)
        raise SystemExit(4)


if __name__=="__main__":
    main()
