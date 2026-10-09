#!/usr/bin/env python3
"""One disposable research witness hosted on xwing, invoked over SSH.

Not a production admission service. The holder argument is NOT authenticated:
two SSH callers using the same identity can claim the same holder. This is a
contract experiment, not a malicious-worker or takeover security boundary.
"""
from dataclasses import asdict
import argparse
import json
import os
import socket
import sys

from assistx.trace_two_host_witness_research import Witness, bootstrap


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=(
        "bootstrap", "observe", "admit", "effect", "complete", "rotate"))
    parser.add_argument("--path", required=True)
    parser.add_argument("--epoch", required=True)
    parser.add_argument("--graph", required=True)
    parser.add_argument("--floor", type=int, required=True)
    parser.add_argument("--holder", choices=("x1-370", "xwing"), default="x1-370")
    parser.add_argument("--term", type=int, default=1)
    parser.add_argument("--ref", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--effect-ref", default="")
    parser.add_argument("--next-holder", choices=("x1-370", "xwing"))
    x = parser.parse_args()

    host = socket.gethostname().split(".")[0]
    if host != "xwing":
        raise SystemExit("WITNESS_MUST_RESIDE_ON_XWING")
    if os.environ.get("ASSISTX_TWOHOST_WITNESS_RESEARCH_ONLY") != "disposable":
        raise SystemExit("DISPOSABLE_RESEARCH_OPT_IN_REQUIRED")
    try:
        if x.action == "bootstrap":
            if x.floor != 1 or x.term != 1 or x.ref or x.token:
                raise ValueError("INVALID_BOOTSTRAP_OPTIONS")
            bootstrap(x.path, epoch=x.epoch, graph=x.graph, holder=x.holder)
            decision = {"status":"bootstrapped"}
        else:
            w = Witness(x.path, epoch=x.epoch, graph=x.graph, floor=x.floor)
            if x.action == "observe":
                decision = {"status":"observed", **w.snapshot()}
            elif x.action == "admit":
                decision = asdict(w.admit(x.ref,x.holder,x.term))
            elif x.action == "effect":
                decision = asdict(w.synthetic_effect(
                    x.token,x.holder,x.term,x.effect_ref))
            elif x.action == "complete":
                decision = asdict(w.complete(x.token,x.holder,x.term))
            else:
                decision = asdict(w.rotate(x.next_holder,x.term))
        print(json.dumps({"witness_host":host,"epoch":x.epoch,
                          "graph":x.graph,**decision},sort_keys=True))
    except Exception as e:
        # Disclose no path, token, environment, or provider credentials.
        print(json.dumps({"status":"witness-unavailable",
                          "witness_host":host,"error_class":type(e).__name__}),
              flush=True)
        raise SystemExit(4)


if __name__ == "__main__":
    main()
