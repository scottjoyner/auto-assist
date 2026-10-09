"""Real disposable Neo4j receiver process signs independently observed closure.

The signing key is *generated within the receiver child process* and never
passed to the worker or API. The parent pins the transaction ID and graph
container identity; the receiver independently inspects the disposable Docker
target, observes an active query, terminates the exact ID, checks six times,
and signs the resulting *research-only* observation. Never releases the ledger.

This is NOT durable receiver key custody, cross-host consensus, production
authorization, or end-to-end Bolt-transport proof beyond the local relay.
"""
from __future__ import annotations
from datetime import datetime,timezone
import argparse
import json
import multiprocessing as mp
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from neo4j import Query

from assistx.trace_durable_ledger_research import (
    DurableTraceReadLedger,bootstrap_disposable_fixture,
)
from assistx.trace_receiver_evidence_research import (
    SCHEMA,receiver_sign_only,verify_research_evidence,
)
from probe_trace_physical_observer_526 import (
    NAME,MARKER,_driver,_worker,active_transactions,guarded_endpoint,
)
from probe_trace_bolt_blackhole_526 import LoopbackBoltBlackhole


def _graph_identity():
    """Pinned disposable container identity, not user-supplied graph URI."""
    # Each process invokes the same strict guard before inspecting identity.
    guarded_endpoint()
    row=json.loads(subprocess.run(["docker","inspect",NAME],
         check=True,capture_output=True,text=True,timeout=8).stdout)[0]
    ident=str(row["Id"])
    if len(ident)!=64 or any(c not in "0123456789abcdef" for c in ident):
        raise ValueError("DISPOSABLE_GRAPH_ID_INVALID")
    return ident


def _receiver(epoch,token,query_ref,nonce,marker,expected_tx,queue):
    """Independent observer and signer; private key never enters worker side."""
    try:
        endpoint,_=guarded_endpoint()
        graph_id=_graph_identity()
        with _driver(endpoint) as inspector:
            inspector.verify_connectivity()
            before=active_transactions(inspector,marker)
            if len(before)!=1 or before[0]["transaction_id"]!=expected_tx:
                raise ValueError("EXACT_RUNNING_TRANSACTION_NOT_WITNESSED")
            # Only this child directs server-side termination.
            with inspector.session(database="neo4j") as session:
                rows=[dict(x) for x in session.run(
                    Query("TERMINATE TRANSACTIONS $id "
                          "YIELD transactionId, message "
                          "RETURN transactionId, message",timeout=3.0),
                    id=expected_tx)]
            if (len(rows)!=1 or str(rows[0]["transactionId"])!=expected_tx
                or str(rows[0]["message"])!="Transaction terminated."):
                raise ValueError("TERMINATE_NOT_ACKNOWLEDGED_BY_SERVER")
            later=[]
            for _ in range(6):
                ids={x["transaction_id"] for x in active_transactions(inspector,marker)}
                later.append(expected_tx in ids)
                time.sleep(.10)
            payload={
                "schema":SCHEMA,"epoch":epoch,"token":token,
                "query_ref":query_ref,"receiver_nonce":nonce,
                "graph_container_id":graph_id,
                "server_transaction_id":expected_tx,
                "observed_running_before":True,
                "terminate_command":"TERMINATE TRANSACTIONS",
                "terminate_server_message":"Transaction terminated.",
                "server_reply_exact_id":True,
                "post_termination_same_id_visible":later,
                "observer_source":"guarded-disposable-direct-Neo4j",
                "terminal_verdict":"receiver-observed-terminated",
            }
            # A transient reappearance prevents signing; no optimism here.
            if any(later):
                raise ValueError("QUERY_STILL_VISIBLE_NO_SIGNING")
            # Receiver-child is the ONLY process holding its private key.
            private_key=Ed25519PrivateKey.generate()
            public=private_key.public_key().public_bytes(
                serialization.Encoding.Raw,serialization.PublicFormat.Raw)
            signature=receiver_sign_only(payload,private_key)
            queue.put({"status":"research_receipt","receipt":payload,
                       "signature_hex":signature.hex(),"public_hex":public.hex()})
    except Exception as exc:
        queue.put({"status":"refused","error_type":type(exc).__name__})


def run():
    endpoint,_=guarded_endpoint()
    host=endpoint.removeprefix("bolt://").split(":")[0]
    epoch=str(uuid.uuid4())
    nonce=str(uuid.uuid4())
    marker=MARKER+uuid.uuid4().hex
    query_ref="physical-blackhole-"+uuid.uuid4().hex
    report={
        "schema":"assistx-receiver-key-custody-lab-v1",
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "global_hard_fence_proven":False,
        "durable_receiver_key_escrow_proven":False,
        "authenticated_production_replay_store_proven":False,
        "automatic_admission_release":False,
        "production_data_used":False,
        "receiver_private_key_ever_returned":False,
    }
    with tempfile.TemporaryDirectory(prefix="assistx-trace-ledger-test-",dir="/tmp") as folder:
        path=str(Path(folder)/"trace-ledger-test.sqlite")
        # Throwaway local ledger verifier key deliberately DIFFERENT from
        # the receiver. The new v2 attestation cannot unlock v1 ledger.
        unrelated=Ed25519PrivateKey.generate()
        public=unrelated.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        bootstrap_disposable_fixture(path,epoch,1)
        ledger=DurableTraceReadLedger(path,epoch,public)
        token=ledger.acquire(query_ref).token
        if token is None:raise RuntimeError("FAILED_TO_RESERVE_LOCAL_CAPACITY")
        ctx=mp.get_context("spawn")
        started=ctx.Event()
        worker_output=ctx.Queue()
        with _driver(endpoint) as watcher, LoopbackBoltBlackhole(host,7687) as proxy:
            watcher.verify_connectivity()
            worker=ctx.Process(
                target=_worker,
                args=(f"bolt://127.0.0.1:{proxy.local_port}",marker,started,worker_output))
            worker.start()
            receiver=None
            try:
                if not started.wait(8) or not proxy.accepted.wait(8):
                    raise RuntimeError("WORKER_NOT_READY_FOR_RECEIVER")
                observed=[]
                end=time.monotonic()+7
                while time.monotonic()<end and not observed and worker.is_alive():
                    observed=active_transactions(watcher,marker)
                    if not observed:time.sleep(.03)
                if len(observed)!=1:raise RuntimeError("NO_UNIQUE_PHYSICAL_TRANSACTION")
                txid=observed[0]["transaction_id"]
                report["observed_id"]=txid
                graph_id=_graph_identity()
                proxy.freeze.set()
                time.sleep(.2)
                report["tx_still_seen_when_blackholed"]=any(
                    row["transaction_id"]==txid
                    for row in active_transactions(watcher,marker))
                report["worker_alive_when_blackholed"]=worker.is_alive()
                if not report["tx_still_seen_when_blackholed"]:
                    raise RuntimeError("INCONCLUSIVE_EARLY_TRANSACTION_COMPLETION")
                signed_queue=ctx.Queue()
                receiver=ctx.Process(
                    target=_receiver,
                    args=(epoch,token,query_ref,nonce,marker,txid,signed_queue))
                receiver.start()
                receiver.join(12)
                if receiver.is_alive():
                    receiver.kill()
                    receiver.join(2)
                    raise RuntimeError("RECEIVER_TIMED_OUT")
                try:response=signed_queue.get(timeout=2)
                except Exception as e:raise RuntimeError("RECEIVER_NOT_RESPONDING") from e
                report["receiver_status"]=response["status"]
                if response["status"]!="research_receipt":
                    report["refused_reason"]=response.get("error_type")
                    return report
                evidence=response["receipt"]
                sig=bytes.fromhex(response["signature_hex"])
                pub=bytes.fromhex(response["public_hex"])
                expected={
                    "expected_epoch":epoch,"expected_token":token,
                    "expected_query_ref":query_ref,
                    "expected_receiver_nonce":nonce,
                    "expected_graph_container_id":graph_id,
                    "expected_transaction_id":txid,
                }
                report["receiver_signed_and_bound"]=verify_research_evidence(
                    evidence,sig,pub,**expected)
                report["nonce_replay_wrong_expected_denied"]=not verify_research_evidence(
                    evidence,sig,pub,**{**expected,
                        "expected_receiver_nonce":str(uuid.uuid4())})
                report["signature_hex"]=response["signature_hex"]
                report["receiver_public_hex"]=response["public_hex"]
                report["receipt"]=evidence
                report["durable_ledger_occupancy_after_signed_observation"]=ledger.inspect()
                report["successor_after_signed_observation"]=ledger.acquire("successor").reason
                report["client_still_waiting_after_receiver_signed"]=worker.is_alive()
                if not(report["receiver_signed_and_bound"]
                    and report["nonce_replay_wrong_expected_denied"]
                    and report["durable_ledger_occupancy_after_signed_observation"]==1
                    and report["successor_after_signed_observation"]=="full"):
                    raise RuntimeError("SIGNED_RECEIPT_BYPASSED_NO_RELEASE_BOUNDARY")
                return report
            finally:
                proxy.freeze.clear()
                if worker.is_alive():worker.kill()
                worker.join(3)
                if receiver is not None and receiver.is_alive():
                    receiver.kill()
                    receiver.join(2)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",required=True)
    arg=parser.parse_args()
    if not(arg.output.startswith("/tmp/assistx-trace-receiver-")
           and arg.output.endswith(".json") and ".." not in arg.output):
        raise SystemExit("DISPOSABLE_TMP_OUTPUT_ONLY")
    data=run()
    with open(arg.output,"x",encoding="utf-8") as f:
        json.dump(data,f,indent=2,sort_keys=True)
        f.write("\n")
    print(json.dumps(data,indent=2,sort_keys=True))
