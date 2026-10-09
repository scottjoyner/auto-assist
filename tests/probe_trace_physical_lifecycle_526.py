"""OPT-IN disposable Neo4j 5.26 physical-transaction admission experiment.

Manual only. Never contact production Neo4j or trust caller-supplied URI.
Requires an exact, internal, unexposed Docker fixture. SQLite reservations are
single-host RESEARCH ONLY. Witness private key lives in a separate process,
not a worker. Absence in SHOW TRANSACTIONS is NOT distributed proof under
partitions, Neo4j restart or other host failures.
"""
from __future__ import annotations

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
from neo4j import GraphDatabase

from assistx.trace_durable_ledger_research import (
    CLOSED, RECEIPT_VERSION, DurableTraceReadLedger, _canonical,
    bootstrap_disposable_fixture,
)

NAME = "assistx-physical-trace-probe-20261009"
NETWORK = "assistx-physical-trace-net-20261009"
MARKER = "ASSISTX_DISPOSABLE_PHYSICAL_WITNESS_20261009"
CAPACITY = 3


def _inspect(*args: str):
    p = subprocess.run(["docker", *args], timeout=9, capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


def checked_uri() -> str:
    """Refuse all foreign, bridged-to-host, mounted or port-published DBs."""
    obj = _inspect("inspect", NAME)
    net = _inspect("network", "inspect", NETWORK)
    if len(obj) != 1 or len(net) != 1:
        raise RuntimeError("AMBIGUOUS_RESEARCH_CONTAINER")
    c, n = obj[0], net[0]
    h = c["HostConfig"]
    if (
        c.get("Name") != "/" + NAME
        or not c["State"]["Running"]
        or c["Config"]["Image"] != "neo4j:5.26-enterprise"
        or h["NetworkMode"] != NETWORK
        or set(c["NetworkSettings"]["Networks"]) != {NETWORK}
        or not n.get("Internal")
        or h.get("PortBindings")
        or h["NanoCpus"] > 1_000_000_000
        or h["Memory"] > 1_900_000_000
        or h["Memory"] <= 0
        or any(m["Type"] == "bind" for m in c.get("Mounts", []))
        or not {"NEO4J_AUTH=none", "NEO4J_ACCEPT_LICENSE_AGREEMENT=yes"}.issubset(
            set(c["Config"].get("Env", []))
        )
    ):
        raise RuntimeError("UNSAFE_RESEARCH_TARGET")
    ip = c["NetworkSettings"]["Networks"][NETWORK]["IPAddress"]
    if not ip.startswith("172.") or not ip.endswith(".2"):
        raise RuntimeError("UNEXPECTED_ISOLATED_IP")
    return f"bolt://{ip}:7687"


def driver(uri):
    return GraphDatabase.driver(
        uri, auth=None, connection_timeout=3, connection_acquisition_timeout=4,
        max_connection_pool_size=8,
    )


def _transactions(conn):
    with conn.session(database="system") as s:
        return [dict(row) for row in s.run(
            "SHOW TRANSACTIONS YIELD transactionId, database, currentQuery, status "
            "RETURN transactionId, database, currentQuery, status"
        )]


def _running_worker(uri: str, ready):
    # Explicit intentionally slow, READ-only query on a completely empty
    # disposable graph. No production graph index or data.
    q = (
        "UNWIND range(1, 45000) AS n UNWIND range(1, 45000) AS m "
        "WITH n,m WHERE (n*m)%97=7 RETURN count(*) AS count /* " + MARKER + " */"
    )
    try:
        with driver(uri) as d:
            with d.session(database="neo4j") as s:
                with s.begin_transaction() as tx:
                    ready.send("started")
                    tx.run(q).consume()
    except Exception as e:
        ready.send("error:" + type(e).__name__)


def _witness_process(uri, conn):
    """Synthetic independent process controls only the ephemeral signing key."""
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    watched = {}
    try:
        with driver(uri) as db:
            conn.send({"public": public})
            while True:
                cmd = conn.recv()
                if cmd["kind"] == "shutdown":
                    return
                if cmd["kind"] == "observe":
                    found = None
                    for _ in range(60):
                        rows = _transactions(db)
                        found = next((
                            r["transactionId"] for r in rows
                            if r["database"] == "neo4j"
                            and str(r["currentQuery"] or "").startswith("UNWIND")
                            and MARKER in (r["currentQuery"] or "")
                        ), None)
                        if found:
                            break
                        time.sleep(0.1)
                    if found:
                        watched[found] = MARKER
                    conn.send({"txid": found})
                elif cmd["kind"] == "close":
                    txid = cmd["txid"]
                    if txid not in watched:
                        conn.send({"status": "unobserved"})
                        continue
                    absent_streak = 0
                    for _ in range(45):
                        active = any(r["transactionId"] == txid for r in _transactions(db))
                        absent_streak = 0 if active else absent_streak + 1
                        if absent_streak >= 2:
                            break
                        time.sleep(0.15)
                    if absent_streak < 2:
                        conn.send({"status": "still-active"})
                        continue
                    body = {
                        "version": RECEIPT_VERSION,
                        "epoch": cmd["epoch"],
                        "token": cmd["token"],
                        "query_ref": cmd["query_ref"],
                        "evidence_id": txid,
                        "verdict": CLOSED,
                    }
                    signature = key.sign(_canonical(body))
                    del watched[txid]
                    conn.send({"status": "observed-absent",
                               "receipt": body, "signature": signature})
                else:
                    conn.send({"status": "unknown-command"})
    finally:
        key = None


def _idle_worker(uri, path, epoch, public, ref, start, release, results):
    start.wait(12)
    try:
        ledger = DurableTraceReadLedger(path, epoch, public)
        decision = ledger.acquire(ref)
        if decision.token is None:
            results.put({"ref": ref, "accepted": False, "reason": decision.reason})
            return
        with driver(uri) as db:
            with db.session(database="neo4j") as s:
                tx = s.begin_transaction()
                tx.run("RETURN 1 AS synthetic_probe").consume()
                results.put({"ref": ref, "accepted": True})
                release.wait(12)
                tx.rollback()
    except Exception as exc:
        results.put({"ref": ref, "accepted": False, "reason": type(exc).__name__})


def run():
    if os.getenv("ASSISTX_TRACE_DISPOSABLE_PHYSICAL_PROBE") != "1":
        raise RuntimeError("EXPLICIT_OPT_IN_REQUIRED")
    uri = checked_uri()
    with driver(uri) as d:
        d.verify_connectivity()
        with d.session(database="neo4j") as s:
            version = s.run("CALL dbms.components() YIELD versions RETURN versions[0] AS v").single()["v"]
        if not version.startswith("5.26."):
            raise RuntimeError("VERSION_MISMATCH")
        if any(r["database"] == "neo4j" for r in _transactions(d)):
            raise RuntimeError("FOREIGN_ACTIVE_TRANSACTION_ON_FIXTURE")

    ctx = mp.get_context("spawn")
    result = {
        "schema": "assistx-physical-lifecycle-research-v1",
        "neo4j_version": version,
        "isolated_network": NETWORK,
        "no_published_ports": True,
        "production_access": False,
        "global_distributed_fence_proven": False,
        "transport_blackhole_proven": False,
        "witness_key_persisted": False,
        "runs": [],
    }

    # Witness is a separate process: the query worker has no signing key.
    with tempfile.TemporaryDirectory(prefix="assistx-trace-ledger-test-", dir="/tmp") as folder:
        path = str(Path(folder) / "trace-ledger-test.sqlite")
        epoch = str(uuid.uuid4())
        parent, watcher = ctx.Pipe()
        proc = ctx.Process(target=_witness_process, args=(uri, watcher))
        proc.start()
        if not parent.poll(12):
            raise AssertionError("WITNESS_DID_NOT_START")
        public = parent.recv()["public"]
        bootstrap_disposable_fixture(path, epoch, 1)
        ledger = DurableTraceReadLedger(path, epoch, public)
        decision = ledger.acquire("physical-running-query")
        assert decision.token and ledger.inspect() == 1
        incoming, outgoing = ctx.Pipe(duplex=False)
        worker = ctx.Process(target=_running_worker, args=(uri, outgoing))
        worker.start()
        assert incoming.poll(12) and incoming.recv() == "started"
        parent.send({"kind":"observe"})
        assert parent.poll(12)
        txid = parent.recv()["txid"]
        assert txid and txid.startswith("neo4j-transaction-"), "NEVER_OBSERVED_REAL_QUERY"
        parent.send({"kind":"close", "txid":txid, "token":decision.token,
                     "query_ref":"physical-running-query", "epoch":epoch})
        # A still-running query can NEVER mint a closure receipt.
        assert parent.poll(12)
        assert parent.recv()["status"] == "still-active"
        before = ledger.acquire("attempt-before-physical-closure")
        assert before.token is None and before.reason == "full"
        worker.kill()
        worker.join(4)
        assert worker.exitcode is not None
        after_crash = ledger.acquire("attempt-after-worker-crash")
        assert after_crash.token is None and after_crash.reason == "full"
        parent.send({"kind":"close", "txid":txid, "token":decision.token,
                     "query_ref":"physical-running-query", "epoch":epoch})
        assert parent.poll(12)
        proof = parent.recv()
        assert proof["status"] == "observed-absent", proof
        assert ledger.acknowledge_remote_closure(proof["receipt"], proof["signature"])
        assert ledger.inspect() == 0
        assert not ledger.acknowledge_remote_closure(proof["receipt"], proof["signature"])
        successor = ledger.acquire("successor-after-witness")
        assert successor.token
        result["crash_and_witness"] = {
            "server_transaction_id": txid,
            "saw_active_running_query": True,
            "premature_witness_denied": True,
            "worker_sigkill_exit_code": worker.exitcode,
            "capacity_held_after_worker_death": True,
            "independent_observer_saw_two_absent_snapshots": True,
            "signed_receipt_released_exact_slot": True,
            "replay_rejected": True,
            "successor_only_after_witness": True,
        }
        parent.send({"kind":"shutdown"})
        proc.join(4)
        assert proc.exitcode == 0

    for attempts in (1, 3, 5, 10):
        with tempfile.TemporaryDirectory(prefix="assistx-trace-ledger-test-", dir="/tmp") as folder:
            path = str(Path(folder) / "trace-ledger-test.sqlite")
            epoch = str(uuid.uuid4())
            signer = Ed25519PrivateKey.generate()
            public = signer.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            bootstrap_disposable_fixture(path, epoch, CAPACITY)
            start, release = ctx.Event(), ctx.Event()
            q = ctx.Queue()
            ps = [ctx.Process(target=_idle_worker,
                  args=(uri, path, epoch, public, f"client-{attempts}-{i}",start,release,q))
                  for i in range(attempts)]
            for child in ps:
                child.start()
            start.set()
            rows = [q.get(timeout=20) for _ in ps]
            accepted = sum(x.get("accepted") is True for x in rows)
            assert accepted == min(attempts, CAPACITY), rows
            with driver(uri) as d:
                active = [r for r in _transactions(d) if r["database"] == "neo4j"]
            assert len(active) == accepted, (len(active),accepted)
            assert DurableTraceReadLedger(path,epoch,public).inspect() == accepted
            release.set()
            for child in ps:
                child.join(8)
                if child.is_alive():
                    child.kill();child.join(3)
                assert child.exitcode == 0, child.exitcode
            result["runs"].append({
                "attempts": attempts, "admitted": accepted,
                "denied": attempts - accepted,
                "server_observed_open_transactions": len(active),
                "persisted_occupancy_after_workers_exit": accepted,
            })
    print(json.dumps(result, sort_keys=True, indent=2))
    return result


if __name__ == "__main__":
    run()
