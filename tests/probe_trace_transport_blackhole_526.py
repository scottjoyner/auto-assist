"""OPT-IN: real Bolt transport-blackhole negative with server transaction witness.

Exercises only the exact isolated Neo4j fixture accepted by
probe_trace_physical_lifecycle_526.checked_uri(). No production URI, host
iptables, Caddy, Redis or graph data is touched. The sole local TCP listener
binds loopback and forwards to the inspected container while it is healthy.
A blackout stops both proxy directions without disconnecting its server
socket. This exposes the difference between a dead worker and active Neo4j.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import re
import select
import socket
import socketserver
import tempfile
import threading
import time
import uuid
from pathlib import Path

from neo4j import GraphDatabase, Query

from assistx.trace_durable_ledger_research import (
    DurableTraceReadLedger, bootstrap_disposable_fixture,
)
from probe_trace_physical_lifecycle_526 import (
    _transactions, _witness_process, checked_uri, driver,
)


class _Proxy(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = False
    daemon_threads = True
    block_on_close = False
    def __init__(self, address, target_ip):
        self.target_ip = target_ip
        self.blackout = threading.Event()
        self.quit = threading.Event()
        super().__init__(address, _Relay)


class _Relay(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            upstream = socket.create_connection((self.server.target_ip, 7687), timeout=3)
        except OSError:
            return
        with upstream:
            self.request.settimeout(0.25)
            upstream.settimeout(0.25)
            sockets = (self.request, upstream)
            while not self.server.quit.is_set():
                if self.server.blackout.is_set():
                    # Both sockets stay OPEN, even if the local client dies.
                    time.sleep(0.08)
                    continue
                try:
                    readable,_,_ = select.select(sockets, [], [], 0.12)
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        peer = upstream if source is self.request else self.request
                        peer.sendall(data)
                except (OSError, ValueError):
                    return


def _unbounded_worker(uri, out):
    # The huge synthetic CPU query is capped by a 20-second server deadline.
    # It runs on the empty disposable graph. No writes or production data.
    query = (
        "UNWIND range(1, 100000) AS n UNWIND range(1, 100000) AS m "
        "WITH n,m WHERE (n*m)%97=7 RETURN count(*) AS count "
        "/* ASSISTX_DISPOSABLE_PHYSICAL_WITNESS_20261009 */"
    )
    try:
        with GraphDatabase.driver(uri, auth=None, connection_timeout=3) as d:
            with d.session(database="neo4j") as s:
                with s.begin_transaction(timeout=20) as tx:
                    out.send("started")
                    tx.run(query).consume()
                    out.send("completed")
    except Exception as ex:
        try:
            out.send("error:" + type(ex).__name__)
        except Exception:
            pass


def run():
    import os
    if os.getenv("ASSISTX_TRACE_DISPOSABLE_PHYSICAL_PROBE") != "1":
        raise RuntimeError("EXPLICIT_OPT_IN_REQUIRED")
    real_uri = checked_uri()
    host_ip = real_uri.removeprefix("bolt://").split(":")[0]
    with driver(real_uri) as d:
        d.verify_connectivity()
        assert not [r for r in _transactions(d) if r["database"] == "neo4j"]

    ctx = mp.get_context("spawn")
    proxy = _Proxy(("127.0.0.1", 0), host_ip)
    listener = threading.Thread(target=proxy.serve_forever, daemon=True)
    listener.start()
    witness = None
    worker = None
    try:
        with tempfile.TemporaryDirectory(prefix="assistx-trace-ledger-test-", dir="/tmp") as root:
            path = str(Path(root) / "trace-ledger-test.sqlite")
            epoch = str(uuid.uuid4())
            parent, child = ctx.Pipe()
            witness = ctx.Process(target=_witness_process, args=(real_uri,child))
            witness.start()
            assert parent.poll(12), "WITNESS_UNAVAILABLE"
            public = parent.recv()["public"]
            bootstrap_disposable_fixture(path, epoch, 1)
            ledger = DurableTraceReadLedger(path, epoch, public)
            reservation = ledger.acquire("blackhole-remote-query")
            assert reservation.token and ledger.inspect() == 1
            r,w = ctx.Pipe(duplex=False)
            worker = ctx.Process(
                target=_unbounded_worker,
                args=(f"bolt://127.0.0.1:{proxy.server_address[1]}",w),
            )
            worker.start()
            assert r.poll(12) and r.recv() == "started", "WORKER_QUERY_UNAVAILABLE"
            parent.send({"kind":"observe"})
            assert parent.poll(12), "WITNESS_NO_VISIBLE_QUERY"
            txid = parent.recv()["txid"]
            assert txid and re.fullmatch(r"neo4j-transaction-[0-9]+", txid), txid
            proxy.blackout.set()
            time.sleep(0.15)
            worker.kill()
            worker.join(4)
            assert worker.exitcode == -9

            # Worker death is not remote query termination: we expect the
            # server transaction to remain while proxy retains the upstream.
            with driver(real_uri) as d:
                first = any(row["transactionId"] == txid for row in _transactions(d))
                time.sleep(0.4)
                second = any(row["transactionId"] == txid for row in _transactions(d))
            assert first and second, "BLACKHOLE_NOT_REPRODUCED"
            denied = ledger.acquire("would-be-successor-after-worker-death")
            assert denied.token is None and denied.reason == "full"

            # This command is deliberately restricted to the witnessed
            # transaction on the checked disposable graph, never production.
            with driver(real_uri) as d:
                with d.session(database="system") as s:
                    response = [dict(v) for v in s.run(
                        "TERMINATE TRANSACTIONS $txid "
                        "YIELD transactionId, message RETURN transactionId, message",
                        txid=txid,
                    )]
            assert len(response) == 1 and response[0]["transactionId"] == txid
            assert "terminated" in response[0]["message"].lower(), response

            # A successful TERMINATE command may mark a query cancelled
            # without making the old transaction disappear: the intentionally
            # stalled Bolt socket is still open. NEVER mint or accept a
            # receipt solely from that command response.
            parent.send({"kind":"close", "txid":txid, "token":reservation.token,
                         "query_ref":"blackhole-remote-query", "epoch":epoch})
            assert parent.poll(12), "POST_TERMINATE_WITNESS_UNAVAILABLE"
            preliminary = parent.recv()
            assert preliminary["status"] == "still-active", preliminary
            assert ledger.inspect() == 1
            assert ledger.acquire("post-terminate-command-pre-physical-closure").token is None

            # Now end the transport on BOTH ends, rather than merely making
            # the client disappear. Only then ask the independent witness
            # for a signed observed-absence receipt.
            proxy.quit.set()
            proxy.blackout.clear()
            parent.send({"kind":"close", "txid":txid, "token":reservation.token,
                         "query_ref":"blackhole-remote-query", "epoch":epoch})
            assert parent.poll(12), "CLOSURE_WITNESS_UNAVAILABLE"
            proof = parent.recv()
            assert proof["status"] == "observed-absent", proof
            assert ledger.acknowledge_remote_closure(proof["receipt"], proof["signature"])
            assert ledger.inspect() == 0
            assert ledger.acquire("successor-only-after-observed-physical-closure").token
            parent.send({"kind":"shutdown"})
            witness.join(4)
            assert witness.exitcode == 0
            report = {
                "schema":"assistx-transport-blackhole-research-v1",
                "neo4j":"disposable 5.26.30",
                "transaction_id":txid,
                "worker_exit_code":worker.exitcode,
                "proxy_bound_loopback_only":True,
                "blackhole_retained_server_socket":True,
                "server_transaction_active_after_worker_sigkill":True,
                "capacity_denied_during_ambiguous_state":True,
                "independent_terminate_response":response[0]["message"],
                "terminate_command_alone_did_not_close_transaction":True,
                "slot_held_until_stalled_transport_closed":True,
                "witness_observed_absence_then_signed":True,
                "signed_receipt_released_one_slot":True,
                "distributed_and_partition_safety_proven":False,
                "production_access":False,
            }
            print(json.dumps(report,sort_keys=True,indent=2))
            return report
    finally:
        if worker and worker.is_alive():
            worker.kill();worker.join(3)
        if witness and witness.is_alive():
            witness.kill();witness.join(3)
        proxy.quit.set()
        proxy.shutdown()
        proxy.server_close()
        listener.join(3)


if __name__ == "__main__":
    run()
