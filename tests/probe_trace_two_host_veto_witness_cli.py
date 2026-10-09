"""#148 isolated secondary xwing witness CLI, not a failover endpoint.

Must run ONLY on xwing with exact research opt-in, disposable /tmp directory,
preexisting SSH host-key trust and no production queries or services. The
bootstrap response is key enrollment material, not an endorsement. It is
exchanged *before* admission requests; the primary must pin it separately.
"""
from __future__ import annotations
import argparse
import json
import os
import socket
import sys

from assistx.trace_two_host_veto_witness_research import (
    VetoWitnessResearch, bootstrap_disposable_witness,
)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("action",choices=("bootstrap","endorse","observe"))
    ap.add_argument("--folder",required=True)
    ap.add_argument("--epoch",required=True)
    ap.add_argument("--graph-id",required=True)
    ap.add_argument("--signer-digest",default="")
    ap.add_argument("--minimum-sequence",type=int,default=0)
    ap.add_argument("--token",default="")
    ap.add_argument("--nonce",default="")
    ap.add_argument("--query-ref",default="")
    ap.add_argument("--sequence",type=int,default=0)
    args=ap.parse_args()
    host=socket.gethostname().split(".")[0]
    if (os.getenv("ASSISTX_TWOHOST_RESEARCH_ONLY")!="yes-disposable"
        or host!="xwing"):
        raise SystemExit("RESEARCH_HOST_OR_OPTIN_REQUIRED")
    try:
        if args.action=="bootstrap":
            if args.signer_digest or args.sequence or args.token or args.nonce:
                raise ValueError("BOOTSTRAP_NOT_READMISSION")
            created=bootstrap_disposable_witness(args.folder,args.epoch,args.graph_id)
            result={
                "status":"bootstrapped-not-authorized","host":host,
                "signer_sha256":created["signer_sha256"],
                "public_key_hex":created["public_key"].hex(),
            }
        else:
            witness=VetoWitnessResearch(
                args.folder,pinned_epoch=args.epoch,pinned_graph_id=args.graph_id,
                pinned_signer_digest=args.signer_digest,
                independently_pinned_minimum_sequence=args.minimum_sequence)
            result=(
                witness.inspect() if args.action=="observe" else
                witness.endorse(token=args.token,receiver_nonce=args.nonce,
                                query_ref=args.query_ref,sequence=args.sequence)
            )
            result={"host":host,**result}
        print(json.dumps(result,sort_keys=True))
    except Exception as exc:
        print(json.dumps({"host":host,"status":"witness-unavailable",
                          "error_class":type(exc).__name__}))
        raise SystemExit(4)


if __name__=="__main__":
    main()
