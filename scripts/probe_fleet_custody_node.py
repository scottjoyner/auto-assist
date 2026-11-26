"""Bounded single-node diagnostic for a stalled custody inventory.

No receipt publication, token aggregation or archive reads beyond manifest
metadata. Timeout and child failure are explicitly incomplete, never absence.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from fleet_burn_custody import DEFAULT_NODES, inspect_node


def probe(root, node, seconds, *, runner=subprocess.run):
    if node not in DEFAULT_NODES:
        raise ValueError("node not in reviewed roster")
    try:
        result=runner([sys.executable,"-B",str(Path(__file__).resolve()),
                       "--child","--root",str(root),"--node",node],
                      stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                      text=True,timeout=seconds,check=False)
        if result.returncode:
            return {"node":node,"verdict":"INCOMPLETE","reason":"child_exit"}
        row=json.loads(result.stdout)
        if row.get("node")!=node or row.get("status") not in (
            "MANIFEST_CHECKED","STALE","QUARANTINED"):
            raise ValueError("invalid child response")
        return {"node":node,"verdict":"METADATA_ONLY","status":row["status"],
                "reason":row.get("reason"),"age_hours":row.get("age_hours"),
                "archive_hash_verified":False,"token_aggregation_qualified":False}
    except subprocess.TimeoutExpired:
        return {"node":node,"verdict":"INCOMPLETE","reason":"metadata_timeout"}
    except (OSError,ValueError,TypeError,KeyError):
        return {"node":node,"verdict":"INCOMPLETE","reason":"child_response_invalid"}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,default=Path("/nas/opencode-fleet"))
    p.add_argument("--node",required=True,choices=DEFAULT_NODES)
    p.add_argument("--seconds",type=int,default=5,choices=range(1,11))
    p.add_argument("--child",action="store_true",help=argparse.SUPPRESS)
    a=p.parse_args()
    if a.child:
        print(json.dumps(inspect_node(a.root,a.node,now=time.time(),max_sha_bytes=0)))
        return 0
    row=probe(a.root,a.node,a.seconds)
    print(json.dumps(row,sort_keys=True))
    return 2 if row["verdict"]=="INCOMPLETE" else 0


if __name__=="__main__":
    sys.exit(main())
