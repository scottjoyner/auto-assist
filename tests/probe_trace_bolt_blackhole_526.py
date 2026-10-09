"""Bounded Bolt transport blackhole against ONLY a disposable Neo4j 5.26 lab.

A loopback-only Python TCP relay pauses forwarding (both directions) WITHOUT
dropping either connection. An independent direct Bolt observer watches the
Neo4j server transaction, sends an exact-ID termination, and observes closure.
No firewall changes, production URI, provider call, or receipt/slot release.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import multiprocessing as mp
import select
import socket
import threading
import time
import uuid

from neo4j import Query

from assistx.trace_durable_ledger_research import DurableTraceReadLedger, bootstrap_disposable_fixture
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from probe_trace_physical_observer_526 import (
    MARKER, _driver, _worker, active_transactions, guarded_endpoint, version,
)


class LoopbackBoltBlackhole:
    """One synthetic session; no forwarding to non-disposable server allowed."""

    def __init__(self, upstream_host: str, upstream_port: int):
        if not upstream_host.startswith("172.") or upstream_port != 7687:
            raise ValueError("NON_DISPOSABLE_UPSTREAM")
        self.upstream = (upstream_host, upstream_port)
        self.freeze = threading.Event()
        self.stop = threading.Event()
        self.listening = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listening.bind(("127.0.0.1", 0))
        self.listening.listen(1)
        self.listening.settimeout(.2)
        self.local_port = self.listening.getsockname()[1]
        self._sockets = []
        self.forwarded = [0, 0]
        self.accepted = threading.Event()
        self._threads = []
        self.worker = threading.Thread(target=self._accept, daemon=True)
        self.worker.start()

    def _relay(self, src: socket.socket, dst: socket.socket, direction: int):
        try:
            src.setblocking(False)
            dst.setblocking(False)
            while not self.stop.is_set():
                if self.freeze.is_set():
                    time.sleep(.015)
                    continue
                readable, _, _ = select.select([src], [], [], .10)
                if not readable:
                    continue
                try:
                    packet = src.recv(65536)
                except BlockingIOError:
                    continue
                if not packet:
                    return
                # At most 65k buffered per direction. Abort safely on
                # backpressure; never silently reopen a new Bolt connection.
                sent = 0
                while sent < len(packet) and not self.stop.is_set():
                    if self.freeze.is_set():
                        time.sleep(.015)
                        continue
                    _, writable, _ = select.select([], [dst], [], .10)
                    if writable:
                        sent += dst.send(packet[sent:])
                        self.forwarded[direction] += sent > 0
        except (OSError, ValueError):
            pass
        finally:
            self.stop.set()

    def _accept(self):
        client = None
        remote = None
        try:
            client, _ = self.listening.accept()
            remote = socket.create_connection(self.upstream, timeout=3)
            self._sockets = [client, remote]
            self.accepted.set()
            t1 = threading.Thread(target=self._relay,args=(client,remote,0),daemon=True)
            t2 = threading.Thread(target=self._relay,args=(remote,client,1),daemon=True)
            self._threads = [t1,t2]
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        except (socket.timeout, OSError):
            self.stop.set()
        finally:
            for s in (client, remote):
                if s is not None:
                    try: s.close()
                    except OSError: pass

    def close(self):
        self.stop.set()
        try:self.listening.close()
        except OSError:pass
        for s in self._sockets:
            try:s.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            try:s.close()
            except OSError:pass
        self.worker.join(1)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def run():
    endpoint, containment = guarded_endpoint()
    host = endpoint.removeprefix("bolt://").split(":")[0]
    epoch = str(uuid.uuid4())
    result = {
        "schema":"assistx-bolt-blackhole-physical-research-v1",
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "neo4j_target":containment,
        "client_transport":"loopback TCP relay, forwarding deliberately paused",
        "production_access":False,
        "global_admission_proven":False,
        "server_closure_receipt_signed":False,
        "automatic_release":False,
        "observations":[],
    }

    # The signing key only establishes the validator's public-key fixture.
    # It is never passed to the worker, proxy, or observer, and is never used
    # to reclaim physical capacity.
    with __import__("tempfile").TemporaryDirectory(prefix="assistx-trace-ledger-test-",dir="/tmp") as folder:
        filepath=str(Path(folder)/"trace-ledger-test.sqlite")
        pub=Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        bootstrap_disposable_fixture(filepath,epoch,1)
        ledger=DurableTraceReadLedger(filepath,epoch,pub)
        token=ledger.acquire("blackhole-physical-worker").token
        if not token:
            raise RuntimeError("CONSERVATIVE_SLOT_NOT_RESERVED")
        ctx=mp.get_context("spawn")
        ready=ctx.Event()
        worker_output=ctx.Queue()
        marker=MARKER+uuid.uuid4().hex
        with _driver(endpoint) as observer, LoopbackBoltBlackhole(host,7687) as proxy:
            observer.verify_connectivity()
            result["neo4j_version"]=version(observer)
            assert not active_transactions(observer,marker)
            proc=ctx.Process(target=_worker,args=(f"bolt://127.0.0.1:{proxy.local_port}",marker,ready,worker_output))
            proc.start()
            try:
                started_ok = ready.wait(6)
                accepted_ok = proxy.accepted.wait(6)
                if not (started_ok and accepted_ok):
                    report = None
                    try:
                        report = worker_output.get_nowait()
                    except Exception:
                        pass
                    raise RuntimeError(
                        "WORKER_OR_PROXY_NOT_READY: "
                        f"ready={started_ok} accepted={accepted_ok} "
                        f"worker_alive={proc.is_alive()} relay_stopped={proxy.stop.is_set()} "
                        f"worker_outcome={report}"
                    )
                observed=[]
                deadline=time.monotonic()+7
                while time.monotonic()<deadline and proc.is_alive() and not observed:
                    observed=active_transactions(observer,marker)
                    if not observed: time.sleep(.04)
                if len(observed)!=1:
                    result["inconclusive"]="server_transaction_not_uniquely_visible"
                    return result
                server_id=observed[0]["transaction_id"]
                result["server_transaction_id"]=server_id
                proxy.freeze.set()
                result["blackhole_enabled"]=True
                result["client_connection_still_open"]=proc.is_alive()
                time.sleep(.30)
                while_blackholed=active_transactions(observer,marker)
                result["server_tx_visible_during_blackhole"]=any(
                    x["transaction_id"]==server_id for x in while_blackholed)
                result["successor_with_redis_independent_ledger"]=ledger.acquire(
                    "blackholed-successor").reason
                result["slot_occupied_during_blackhole"]=ledger.inspect()
                if result["successor_with_redis_independent_ledger"]!="full":
                    raise RuntimeError("UNSAFE_SUCCESSOR_ADMITTED")
                # Observer may send termination independently via direct Bolt,
                # while both directions of the *worker* Bolt stream are paused.
                with observer.session(database="neo4j") as session:
                    rows=[dict(x) for x in session.run(
                        Query("TERMINATE TRANSACTIONS $id YIELD transactionId, message "
                              "RETURN transactionId, message",timeout=3.0),
                        id=server_id)]
                result["admin_terminal_response"]=[
                    {"transaction_id":str(x["transactionId"]),
                     "message":str(x["message"])[:100]} for x in rows]
                result["admin_response_matches_exact_id"]=(
                    len(rows)==1 and str(rows[0]["transactionId"])==server_id)
                for i in range(6):
                    live=active_transactions(observer,marker)
                    result["observations"].append({
                        "sample":i,"old_id_still_listed":any(
                            x["transaction_id"]==server_id for x in live)
                    })
                    time.sleep(.12)
                result["worker_alive_while_transport_blackholed"]=proc.is_alive()
                result["slot_still_occupied_after_terminate"]=ledger.inspect()
                result["admission_after_server_termination"]=ledger.acquire(
                    "new-worker-after-terminate").reason
                proxy.freeze.clear()  # cleanup only; no slot reclamation
                proc.join(3)
                result["worker_exited_after_unfreeze"]=not proc.is_alive()
                result["independent_receipt_not_authorized"]=True
            finally:
                if proc.is_alive():proc.kill()
                proc.join(3)
    return result


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True)
    opt=p.parse_args()
    if not (opt.output.startswith("/tmp/assistx-trace-blackhole-")
            and opt.output.endswith(".json") and ".." not in opt.output):
        raise SystemExit("ONLY_DISPOSABLE_TMP_OUTPUT")
    data=run()
    with open(opt.output,"x",encoding="utf-8") as f:
        json.dump(data,f,indent=2,sort_keys=True)
        f.write("\n")
    print(json.dumps(data,indent=2,sort_keys=True))
