"""Physical read vs durable slot under disposable Redis loss, research ONLY.

Uses the independently guarded Neo4j observer with a separate driver and
isolated worker. Tests a real Redis 7 memory-only restart while the real
Neo4j transaction is observed, yet the durable ledger continues to deny
successor admission. No signed closure or production API integration.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from assistx.trace_durable_ledger_research import (
    DurableTraceReadLedger, bootstrap_disposable_fixture,
)
from probe_trace_physical_observer_526 import (
    NAME, NETWORK, MARKER, _driver, _worker, active_transactions,
    guarded_endpoint, version,
)

REDIS_NAME = "assistx-trace-redis-physical-20261009"
REDIS_IMAGE = "redis:7-alpine"
KEY = "assistx-synthetic-redis-slot-restart"

def _exec(*args):
    return subprocess.run(["docker", *args],check=True,capture_output=True,
                          text=True,timeout=15).stdout.strip()

def _verify_redis_target():
    c=json.loads(_exec("inspect",REDIS_NAME))[0]
    hc=c["HostConfig"]
    net=c["NetworkSettings"]["Networks"]
    if (c["Name"]!="/"+REDIS_NAME
        or c["Config"]["Image"]!=REDIS_IMAGE
        or c["Config"].get("Labels",{}).get("assistx.research.physical")!="20261009"
        or hc["NetworkMode"]!=NETWORK or set(net)!={NETWORK}
        or hc.get("PortBindings") or hc.get("Binds")
        or any(x["Type"]=="bind" for x in c.get("Mounts",[]))
        or not (0<hc.get("NanoCpus",0)<=500_000_000)
        or not (0<hc.get("Memory",0)<=201_326_592)
        or not c["State"]["Running"]):
        raise RuntimeError("NOT_DISPOSABLE_REDIS")

def _redis(*args):
    _verify_redis_target()
    return _exec("exec",REDIS_NAME,"redis-cli",*args)

def _contend(ledger, n):
    def attempt(i):
        # Every request has a distinct source ID; all must fail when cap=1
        # is held by the earlier actual Neo4j query.
        return ledger.acquire("successor-"+str(n)+"-"+str(i)).reason
    with ThreadPoolExecutor(max_workers=n) as pool:
        outcomes=list(pool.map(attempt,range(n)))
    if outcomes != ["full"]*n:
        raise RuntimeError("PHYSICAL_SUCCESSOR_ADMITTED")
    return {"attempts":n, "admitted":0, "denied":n,"denied_reasons":{"full":n}}

def run():
    uri, guard=guarded_endpoint()
    _verify_redis_target()
    assert _redis("PING")=="PONG"
    key_value=_redis("GET",KEY)
    if key_value:
        raise RuntimeError("DIRTY_REDIS_RESEARCH_INITIAL_STATE")
    result={
        "schema":"assistx-physical-admission-redis-loss-research-v1",
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "neo4j_image":guard["image"], "production_data":False,
        "production_services_touched":False,
        "global_hard_fence_proven":False,
        "physically_confirmed_remote_termination":False,
        "independent_signer_receipt_created":False,
        "failure_mode":"disposable_redis_7_restart_during_real_read",
        "waves":[],
    }
    with tempfile.TemporaryDirectory(prefix="assistx-trace-ledger-test-",dir="/tmp") as folder:
        p=str(Path(folder)/"trace-ledger-test.sqlite")
        epoch=str(uuid.uuid4())
        key=Ed25519PrivateKey.generate()
        public=key.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        bootstrap_disposable_fixture(p,epoch,1)
        ledger=DurableTraceReadLedger(p,epoch,public)
        token=ledger.acquire("physical-worker").token
        if not token or ledger.inspect()!=1:
            raise RuntimeError("FIRST_PHYSICAL_ADMISSION_FAILED")
        result["initial_physical_slot"]="reserved_and_durable"
        marker=MARKER+uuid.uuid4().hex
        context=mp.get_context("spawn")
        ready=context.Event()
        output=context.Queue()
        with _driver(uri) as independent:
            independent.verify_connectivity()
            result["neo4j_version"]=version(independent)
            assert not active_transactions(independent,marker)
            worker=context.Process(target=_worker,args=(uri,marker,ready,output))
            worker.start()
            try:
                if not ready.wait(7):
                    raise RuntimeError("PHYSICAL_WORKER_NOT_STARTED")
                start=time.monotonic()
                physical=[]
                while time.monotonic()-start<7 and worker.is_alive() and not physical:
                    physical=active_transactions(independent,marker)
                    if not physical:time.sleep(.05)
                if len(physical)!=1:
                    result["inconclusive"]="server_transaction_not_observed"
                    return result
                txid=physical[0]["transaction_id"]
                result["server_transaction_id"]=txid
                result["physically_active_before_redis_restart"]=True

                # Real external-memory cache loss. Only the *throwaway*
                # no-persistence Redis container is restarted.
                assert _redis("SET",KEY,"occupied")=="OK"
                assert _redis("GET",KEY)=="occupied"
                _exec("restart","--time","1",REDIS_NAME)
                result["redis_key_missing_after_restart"]=(_redis("GET",KEY)=="")
                if not result["redis_key_missing_after_restart"]:
                    raise RuntimeError("REDIS_STATE_LOSS_NOT_REPRODUCED")
                result["prior_physical_query_still_running_at_restart"]=any(
                    x["transaction_id"]==txid for x in active_transactions(independent,marker))
                for contenders in [1,3,5,10]:
                    result["waves"].append(_contend(ledger,contenders))
                result["slot_count_before_worker_crash"]=ledger.inspect()
                worker.kill()
                worker.join(4)
                result["worker_exitcode"]=worker.exitcode
                result["slot_count_after_worker_crash"]=ledger.inspect()
                result["subsequent_query_admission"]=ledger.acquire("after-worker-death").reason
                observations=[]
                for i in range(5):
                    active=active_transactions(independent,marker)
                    observations.append({"sample":i,"same_id_visible":any(
                        x["transaction_id"]==txid for x in active)})
                    time.sleep(.12)
                result["post_crash_observation"]=observations
                result["physical_query_observation_is_only_listing"]=True
                if not (result["slot_count_after_worker_crash"]==1
                        and result["subsequent_query_admission"]=="full"
                        and all(w["admitted"]==0 for w in result["waves"])):
                    raise RuntimeError("FAIL_CLOSED_CAPACITY_BROKEN")
            finally:
                if worker.is_alive():worker.kill()
                worker.join(4)
                # This intentionally NEVER calls acknowledge_remote_closure.
    return result

if __name__=="__main__":
    import argparse
    a=argparse.ArgumentParser()
    a.add_argument("--output",required=True)
    args=a.parse_args()
    if not(args.output.startswith("/tmp/assistx-trace-physical-")
           and args.output.endswith(".json")
           and ".." not in args.output):
        raise SystemExit("UNSAFE_OUTPUT")
    result=run()
    with open(args.output,"x",encoding="utf-8") as f:
        json.dump(result,f,sort_keys=True,indent=2)
        f.write("\n")
    print(json.dumps(result,sort_keys=True,indent=2),flush=True)
