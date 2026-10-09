"""Neo4j 5.26 server-initiated transaction termination, disposable lab ONLY.

An independently connected observer captures a server-assigned transaction ID
for an invented read-only workload, asks Neo4j to terminate exactly that ID,
and verifies the server's response and later transaction listings. No signed
release receipt is minted. This is a research boundary, not authority over
production graph transactions or cross-host query admission.
"""
from __future__ import annotations
from datetime import datetime,timezone
import json
import multiprocessing as mp
import time
import uuid

from neo4j import Query, READ_ACCESS
from probe_trace_physical_observer_526 import (
    MARKER, _driver, _worker, active_transactions, guarded_endpoint, version
)


def run():
    uri, containment=guarded_endpoint()
    ctx=mp.get_context("spawn")
    marker=MARKER+uuid.uuid4().hex
    started=ctx.Event()
    worker_output=ctx.Queue()
    report={
        "schema":"assistx-remote-transaction-terminate-research-v1",
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "production_graph":False, "real_server_termination_command":False,
        "independent_observer_signed":False,
        "fleetwide_completion_proven":False,
        "transport_blackhole_tested":False,
        "container":containment
    }
    with _driver(uri) as observer:
        observer.verify_connectivity()
        report["neo4j_version"]=version(observer)
        assert not active_transactions(observer,marker)
        proc=ctx.Process(target=_worker,args=(uri,marker,started,worker_output))
        proc.start()
        try:
            if not started.wait(6):
                raise RuntimeError("WORKER_NOT_STARTED")
            target=[]
            begin=time.monotonic()
            while not target and time.monotonic()-begin<7 and proc.is_alive():
                target=active_transactions(observer,marker)
                if not target:time.sleep(.05)
            if len(target)!=1:
                report["inconclusive"]="exactly_one_server_transaction_not_observed"
                return report
            txid=target[0]["transaction_id"]
            if not txid.startswith("neo4j-transaction-") or not txid[len("neo4j-transaction-"):].isdigit():
                raise RuntimeError("UNKNOWN_TARGET_ID")
            report["previously_observed_transaction_id"]=txid
            # Administrative TERMINATE is executed ONLY against the verified
            # disposable target, with its server-owned exact transaction ID.
            with observer.session(database="neo4j") as session:
                result=session.run(
                    Query("TERMINATE TRANSACTIONS $id "
                          "YIELD transactionId, message "
                          "RETURN transactionId, message",timeout=3.0),
                    id=txid,
                )
                rows=[dict(r) for r in result]
                result.consume()
            report["termination_rows"]=[
                {"transaction_id":str(r["transactionId"]),
                 "message":str(r["message"])[:180]} for r in rows
            ]
            report["real_server_termination_command"]=True
            report["server_response_id_matches"]=(
                len(rows)==1 and str(rows[0]["transactionId"])==txid)
            samples=[]
            for i in range(6):
                active=active_transactions(observer,marker)
                samples.append({"sample":i,"same_id_visible":any(
                    x["transaction_id"]==txid for x in active)})
                time.sleep(.12)
            report["post_termination_observations"]=samples
            proc.join(4)
            report["worker_exited"]=not proc.is_alive()
            if not worker_output.empty():
                report["worker_result"]=worker_output.get_nowait()
            # Deliberately do not sign any receipt or free a durable slot.
            # A valid administration message is not a signed independent
            # transaction-closure event with fleetwide epoch ownership.
            report["signing_key_present"]=False
            report["automatic_admission_release"]=False
        finally:
            if proc.is_alive():proc.kill()
            proc.join(4)
    return report


if __name__=="__main__":
    import argparse
    a=argparse.ArgumentParser()
    a.add_argument("--output",required=True)
    args=a.parse_args()
    if not(args.output.startswith("/tmp/assistx-trace-physical-")
           and args.output.endswith(".json")
           and ".." not in args.output):
        raise SystemExit("ONLY_DISPOSABLE_TMP_OUTPUT")
    report=run()
    with open(args.output,"x",encoding="utf-8") as f:
        json.dump(report,f,sort_keys=True,indent=2)
        f.write("\n")
    print(json.dumps(report,indent=2,sort_keys=True),flush=True)
