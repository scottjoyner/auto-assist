#!/usr/bin/env python3
"""Disposable-loopback-only Neo4j concurrency acceptance for post-hoc receipts.

Never points at production. Requires separate explicit enable flag, a random
non-default localhost port, and an EMPTY fresh graph. No private inventory.
Run in a disposable bounded container, then destroy that container.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
import sys
import uuid

from neo4j import GraphDatabase

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "src"))
from assistx.fleet_hardware_inspection import (  # noqa: E402
    InspectionSchemaUnavailable,
    list_inspections,
    persist_inspection,
    prepare_inspection_receipt,
)


def insist(cond, message):
    if not cond:
        raise AssertionError(message)


def prepare(cid, ram):
    ev = {
        "snapshot": "2026-10-10T16-00-00Z",
        "snapshot_freshness": "FRESH",
        "evidence_digest_sha256": "a" * 64,
        "admission_allowed": False,
        "dispatch_allowed": False,
        "requirements": {"ram_gib": float(ram), "gpu_vram_gib": 16.0, "data_host": None},
        "checked_nodes": 3,
        "truncated": False,
        "nodes": [{"node_id": "synthetic-node", "dispatch": "DENIED_NO_AUTHORITY"}],
    }
    return prepare_inspection_receipt(cid, ev)


class DB:
    def __init__(self, driver):
        self.driver = driver

    def _session(self):
        return self.driver.session(database="neo4j")


def validate_disposable_container(meta: dict, bolt_port: int) -> None:
    """Hard gate: independently prove this port belongs to our bounded lab."""
    if meta.get("Name") != "/assistx-receipt-lab-20261010":
        raise RuntimeError("refusing non-lab container")
    if not meta.get("State", {}).get("Running", False):
        raise RuntimeError("disposable Neo4j must be running")
    image = meta.get("Config", {}).get("Image", "")
    if not re.fullmatch(r"neo4j:5[.][0-9.]+", image):
        raise RuntimeError("unexpected disposable image")
    host = meta.get("HostConfig", {})
    memory = host.get("Memory", 0)
    cpus = host.get("NanoCpus", 0)
    if not (0 < memory <= 2 * 1024**3 and 0 < cpus <= 1_000_000_000):
        raise RuntimeError("disposable Neo4j is not cgroup resource-bounded")
    if host.get("Binds") or any(m.get("Type") == "bind" for m in meta.get("Mounts", [])):
        raise RuntimeError("refusing lab with host bind mounts")
    ports = meta.get("NetworkSettings", {}).get("Ports", {}).get("7687/tcp")
    if not isinstance(ports, list) or len(ports) != 1 or ports[0].get("HostIp") != "127.0.0.1":
        raise RuntimeError("disposable Bolt port must bind only to localhost")
    if str(ports[0].get("HostPort")) != str(bolt_port):
        raise RuntimeError("Bolt URI is not mapped to the disposable container")


def inspect_disposable(url: str) -> None:
    try:
        result = subprocess.run(
            ["docker", "inspect", "assistx-receipt-lab-20261010"],
            capture_output=True,
            text=True,
            check=True,
            timeout=8,
        )
        found = json.loads(result.stdout)
        if not isinstance(found, list) or len(found) != 1:
            raise RuntimeError("disposable container identity unavailable")
        validate_disposable_container(found[0], int(url.rsplit(":", 1)[1]))
    except (OSError, ValueError, subprocess.SubprocessError) as err:
        raise RuntimeError("disposable container could not be independently verified") from err


def main():
    url = os.environ.get("NEO4J_RECEIPT_LAB_URI", "")
    if os.environ.get("ASSISTX_RECEIPT_DISPOSABLE_LAB") != "I_ACCEPT_DISPOSABLE_ONLY":
        raise SystemExit("DENIED: explicit disposable lab acceptance missing")
    if not re.fullmatch(r"bolt://127\.0\.0\.1:[0-9]{4,5}", url) or url.endswith(":7687"):
        raise SystemExit("DENIED: only dynamic nondefault localhost Neo4j Bolt port accepted")
    inspect_disposable(url)
    driver = GraphDatabase.driver(url, auth=None, max_connection_pool_size=15, connection_timeout=6)
    try:
        driver.verify_connectivity()
        with driver.session(database="neo4j") as s:
            version = s.run("CALL dbms.components() YIELD versions RETURN versions[0] AS version").single()["version"]
            insist(version.startswith("5."), "unexpected Neo4j")
            n = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            insist(n == 0, "DENIED: database not disposable empty graph")
            constraints = s.run("SHOW CONSTRAINTS YIELD name RETURN collect(name) AS names").single()["names"]
            insist(not constraints, "DENIED: database has existing schema")
            # DISPOSABLE-ONLY schema migration, never a production endpoint.
            s.run(
                "CREATE CONSTRAINT fleet_hardware_inspection_receipt_id_unique FOR (r:FleetHardwareInspection) REQUIRE r.receipt_id IS UNIQUE"
            ).consume()
            s.run(
                "CREATE CONSTRAINT fleet_hardware_inspection_lock_id_unique FOR (m:FleetHardwareInspectionLock) REQUIRE m.correlation_id IS UNIQUE"
            ).consume()
        db = DB(driver)
        cid = "synthetic-" + uuid.uuid4().hex
        with driver.session(database="neo4j") as s:
            s.run("CREATE (:TraceGroup {correlation_id:$cid,current_state:'completed'})", {"cid": cid}).consume()
        first = prepare(cid, 1.0)
        result = persist_inspection(db, first, authenticated_actor="physical-test")
        insist(result is not None, "single receipt failed")
        print("single_post PASS", flush=True)
        # Concurrent different receipts race at 20. 6 at a time deliberately.
        cases = [prepare(cid, float(i)) for i in range(2, 42)]

        def submit(r):
            try:
                return persist_inspection(db, r, authenticated_actor="physical-test"), None
            except Exception as err:
                return None, type(err).__name__ + ":" + str(err)[:120]

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(submit, cases))
        exceptions = [err for _, err in results if err]
        with driver.session(database="neo4j") as s:
            row = s.run(
                """
                MATCH (g:TraceGroup {correlation_id:$cid})
                OPTIONAL MATCH (g)-[:HAS_HARDWARE_INSPECTION]->(r:FleetHardwareInspection)
                RETURN g.current_state AS state, count(r) AS receipts
                """,
                {"cid": cid},
            ).single()
            receipts = s.run("MATCH (r:FleetHardwareInspection) RETURN count(r) AS n").single()["n"]
            mutex = s.run("MATCH (m:FleetHardwareInspectionLock) RETURN count(m) AS n").single()["n"]
            traces = s.run("MATCH (e:TraceEvent) RETURN count(e) AS n").single()["n"]
        print(
            "concurrent_distinct quota:",
            row["receipts"],
            "receipt_nodes:",
            receipts,
            "mutex:",
            mutex,
            "trace_events:",
            traces,
            "errors:",
            exceptions[:6],
            flush=True,
        )
        insist(row["receipts"] == 20 and receipts == 20, "concurrent quota breach or missed quota")
        insist(row["state"] == "completed", "execution state modified")
        insist(mutex == 1 and traces == 0, "unexpected graph side effects")
        insist(not exceptions, "transient concurrency errors need retry policy")
        # Duplicate replay at quota: all should receive original ID, no writes.
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            dups = list(pool.map(submit, [first] * 12))
        insist(
            all(e is None and r is not None and r["receipt_id"] == first["receipt_id"] for r, e in dups),
            "duplicate lost-ack replay must succeed at quota",
        )
        assert len(list_inspections(db, cid)) == 20
        absent = persist_inspection(
            db, prepare("synthetic-not-existing-" + uuid.uuid4().hex, 3), authenticated_actor="physical-test"
        )
        insist(absent is None, "unrequested TraceGroup creation detected")
        print("idempotent_replay PASS missing_group PASS", flush=True)
        # Verify fail-closed schema gate while still inside disposable DB.
        with driver.session(database="neo4j") as s:
            s.run("DROP CONSTRAINT fleet_hardware_inspection_lock_id_unique").consume()
        try:
            persist_inspection(db, prepare(cid, 43), authenticated_actor="physical-test")
        except InspectionSchemaUnavailable:
            print("missing_mutex_constraint FAIL_CLOSED_PASS", flush=True)
        else:
            raise AssertionError("missing lock schema was erroneously accepted")
        print("DISPOSABLE_PHYSICAL_ACCEPTANCE PASS", flush=True)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
