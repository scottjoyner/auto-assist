"""Independent Neo4j transaction-observation probe, disposable Docker ONLY.

RESEARCH ONLY. Never connect to a configurable URI or read real trace data.
The worker owns the Bolt connection; the observer uses a separate driver and
logs a server-assigned transaction ID. A client timeout / process exit is NOT
a physical cancellation witness. No automated ledger release is permitted.

Safety: exact disposable container name, image, internal network, no published
ports, memory/CPU caps, no host bind mounts, explicit research label.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import multiprocessing as mp
import os
import re
import subprocess
import time
import uuid

from neo4j import GraphDatabase, Query, READ_ACCESS

NAME = "assistx-trace-physical-20261009"
NETWORK = "assistx-trace-physical-net-20261009"
IMAGE = "neo4j:5.26.26-enterprise"
MARKER = "ASSISTX_SYNTHETIC_WITNESS_"
LEDGER_ACTIVE = "capacity_remains_occupied_without_independent_release"


def _docker_inspect(*args: str):
    result = subprocess.run(["docker", *args], capture_output=True, text=True,
                            timeout=8, check=True)
    return json.loads(result.stdout)


def guarded_endpoint() -> tuple[str, dict]:
    obj = _docker_inspect("inspect", NAME)
    if len(obj) != 1:
        raise RuntimeError("DISPOSABLE_CONTAINER_NOT_UNIQUE")
    c = obj[0]
    settings = c["HostConfig"]
    networks = c["NetworkSettings"]["Networks"]
    net = _docker_inspect("network", "inspect", NETWORK)
    assert len(net) == 1
    if (c["Name"] != "/" + NAME
        or c["Config"]["Image"] != IMAGE
        or c["Config"].get("Labels", {}).get("assistx.research.physical") != "20261009"
        or not c["State"]["Running"]
        or settings["NetworkMode"] != NETWORK
        or set(networks) != {NETWORK}
        or not net[0]["Internal"]
        or settings.get("PortBindings")
        or settings.get("Binds")
        or any(m["Type"] == "bind" for m in c.get("Mounts", []))
        or not (0 < settings.get("NanoCpus", 0) <= 1_000_000_000)
        or not (0 < settings.get("Memory", 0) <= 2_415_919_104)
        or "NEO4J_AUTH=none" not in c["Config"]["Env"]):
        raise RuntimeError("UNSAFE_RESEARCH_TARGET")
    ip = networks[NETWORK]["IPAddress"]
    if not re.fullmatch(r"172\.(?:\d{1,3}\.){2}\d{1,3}", ip):
        raise RuntimeError("UNEXPECTED_DISPOSABLE_NETWORK")
    return "bolt://" + ip + ":7687", {
        "image": IMAGE, "internal": True, "published_ports": False,
        "cpu_cap": settings["NanoCpus"], "memory_cap_bytes": settings["Memory"],
        "target_label": "assistx.research.physical=20261009",
    }


def _driver(uri):
    return GraphDatabase.driver(uri, auth=None, connection_timeout=2,
                                connection_acquisition_timeout=2,
                                max_connection_pool_size=2,
                                max_transaction_retry_time=0)


def active_transactions(driver, marker):
    # Parameterized marker avoids ever placing it in this query's literal text.
    statement = ("SHOW TRANSACTIONS YIELD transactionId, currentQuery, status "
                 "WHERE currentQuery CONTAINS $marker "
                 "RETURN transactionId, status")
    with driver.session(database="neo4j", default_access_mode=READ_ACCESS) as session:
        return sorted([{
            "transaction_id": str(row["transactionId"]),
            "status": str(row["status"]),
        } for row in session.run(Query(statement, timeout=2.0), marker=marker)],
                      key=lambda r:r["transaction_id"])


def _worker(uri, marker, started_event, outcome_queue):
    # This process has no ledger-signing material and no lease-reaping ability.
    query = (
        "UNWIND range(1, 16000) AS n UNWIND range(1, 16000) AS m "
        "WITH n,m WHERE (n * m) % 97 = 7 "
        "RETURN count(*) AS synthetic_count /* " + marker + " */"
    )
    try:
        with _driver(uri) as driver:
            with driver.session(database="neo4j", default_access_mode=READ_ACCESS) as s:
                started_event.set()
                result = s.run(Query(query, timeout=18.0))
                result.consume()
        outcome_queue.put({"outcome":"completed"})
    except Exception as exc:
        outcome_queue.put({"outcome":"error", "error_type":type(exc).__name__})


def version(driver):
    with driver.session(database="neo4j", default_access_mode=READ_ACCESS) as session:
        row = session.run("CALL dbms.components() YIELD versions "
                          "RETURN versions[0] AS version").single()
    value = str(row["version"])
    if not value.startswith("5.26."):
        raise RuntimeError("WRONG_NEO4J_VERSION")
    return value


def run() -> dict:
    uri, containment = guarded_endpoint()
    marker = MARKER + uuid.uuid4().hex
    report = {
        "schema":"assistx-physical-query-observer-research-v1",
        "timestamp_utc":datetime.now(timezone.utc).isoformat(),
        "production_graph_used":False, "production_credentials_used":False,
        "neo4j_synthetic_query_only":True,
        "hard_fleet_admission_proven":False,
        "transport_blackhole_cancellation_proven":False,
        "signed_termination_custody_proven":False,
        "container":containment,
        "worker":{}, "observer":{},
    }
    ctx = mp.get_context("spawn")
    started = ctx.Event()
    outcome = ctx.Queue()
    with _driver(uri) as observer:
        observer.verify_connectivity()
        report["neo4j_version"] = version(observer)
        if active_transactions(observer, marker):
            raise RuntimeError("NONEMPTY_DISPOSABLE_MARKER_BEFORE_TEST")
        proc = ctx.Process(target=_worker, args=(uri,marker,started,outcome))
        proc.start()
        if not started.wait(6):
            proc.kill()
            proc.join(4)
            raise RuntimeError("SYNTHETIC_WORKER_FAILED_TO_START")
        try:
            seen=[]
            deadline = time.monotonic()+7.0
            while time.monotonic() < deadline and not seen and proc.is_alive():
                seen=active_transactions(observer, marker)
                if not seen:
                    time.sleep(0.04)
            report["observer"]["physically_seen_before_worker_crash"]=bool(seen)
            report["observer"]["transaction_ids_before_crash"]=list(
                row["transaction_id"] for row in seen)
            if len(seen)!=1:
                report["worker"]["observation_inconclusive"]=True
                return report

            # Abruptly kill the client process, not the server. This is
            # NOT proof of physical cancellation: a severed connection may
            # leave server work behind or may cooperatively cancel.
            proc.kill()
            proc.join(4)
            report["worker"]["exitcode"]=proc.exitcode
            report["worker"]["killed_while_server_query_was_visible"]=True
            observations=[]
            previous_id=seen[0]["transaction_id"]
            vanished_at=None
            begin=time.monotonic()
            while time.monotonic()-begin < 5.0:
                live=active_transactions(observer, marker)
                observations.append({
                    "elapsed_ms":round((time.monotonic()-begin)*1000),
                    "matching_server_transaction_count":len(live),
                    "previous_id_still_visible":any(
                        row["transaction_id"]==previous_id for row in live),
                })
                if not live and len(observations)>2:
                    vanished_at=round((time.monotonic()-begin)*1000)
                    break
                time.sleep(0.12)
            report["observer"]["after_kill_samples"]=observations[:50]
            report["observer"]["no_longer_listed_after_ms"]=vanished_at
            report["observer"]["same_transaction_id_observed"]=previous_id
            report["observer"]["server_termination_receipt_signed"]=False
            report["observer"]["neo4j_transaction_closure_proven"]=False
            report["admission_after_crash"]=LEDGER_ACTIVE
            # Do NOT turn absence of SHOW rows into an Ed25519 receipt.
            # A separate observer with authority over transaction IDs, and
            # independently held signing keys, is still needed for release.
            if active_transactions(observer, marker):
                report["observer"]["still_running_after_poll"]=True
            with observer.session(database="neo4j",default_access_mode=READ_ACCESS) as session:
                report["observer"]["server_still_responds"] = (
                    session.run("RETURN 1 AS ok").single()["ok"] == 1)
        finally:
            if proc.is_alive():
                proc.kill()
            proc.join(4)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True,
                        help="JSON path under /tmp/assistx-trace-physical-*")
    args=parser.parse_args()
    if not (args.output.startswith("/tmp/assistx-trace-physical-")
            and args.output.endswith(".json")
            and "/../" not in args.output):
        raise SystemExit("ONLY_DISPOSABLE_TMP_OUTPUT_ALLOWED")
    data=run()
    with open(args.output,"x",encoding="utf-8") as file:
        json.dump(data,file,indent=2,sort_keys=True)
        file.write("\n")
    print(json.dumps(data,indent=2,sort_keys=True),flush=True)
