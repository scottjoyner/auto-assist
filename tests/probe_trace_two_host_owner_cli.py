#!/usr/bin/env python3
"""Execute a ONE-owner disposable #148 research request over existing SSH.

No listener, port, code execution from receipt, credential handling, global
lease repair, read workload, or admission-slot release. Host must be x1-370
or xwing and fixture path must be under /tmp/assistx-twohost-authority-test-*.
For a physical two-origin experiment, callers run this CLI *over SSH* on
the single owner. Connection errors are deny-all, never local fallback.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import socket
import sys

from assistx.trace_two_host_authority_research import (
    SingleAuthorityResearch, bootstrap_research,
)


def main():
    a=argparse.ArgumentParser()
    a.add_argument("action",choices=("bootstrap","admit","observe"))
    a.add_argument("--path",required=True)
    a.add_argument("--epoch",required=True)
    a.add_argument("--graph-id",required=True)
    a.add_argument("--minimum-sequence",required=True,type=int)
    a.add_argument("--query-ref",default="")
    a.add_argument("--require-host",choices=("x1-370","xwing"),required=True)
    x=a.parse_args()
    host=socket.gethostname().split(".")[0]
    if os.environ.get("ASSISTX_TWOHOST_RESEARCH_ONLY")!="yes-disposable":
        raise SystemExit("RESEARCH_OPT_IN_REQUIRED")
    if host!=x.require_host:
        raise SystemExit("HOST_IDENTITY_MISMATCH")
    try:
        if x.action=="bootstrap":
            if x.minimum_sequence!=0 or x.query_ref:
                raise ValueError("INVALID_BOOTSTRAP_PARAMETERS")
            bootstrap_research(x.path,epoch=x.epoch,graph_id=x.graph_id)
            print(json.dumps({"status":"bootstrapped","host":host},sort_keys=True))
            return
        owner=SingleAuthorityResearch(
            x.path,pinned_epoch=x.epoch,pinned_graph_id=x.graph_id,
            minimum_sequence=x.minimum_sequence)
        if x.action=="observe":
            result={"status":"observed","host":host,**owner.snapshot()}
        else:
            result={"host":host,**asdict(owner.admit(x.query_ref))}
        print(json.dumps(result,sort_keys=True))
    except Exception as e:
        # Avoid leaking file paths/secrets/data in error output.
        print(json.dumps({"status":"authority-unavailable","host":host,
                          "error_class":type(e).__name__}),flush=True)
        raise SystemExit(4)


if __name__=="__main__":
    main()
