#!/usr/bin/env python3
"""Physically exercise an UNWIRED identity-writer candidate in disposable Neo4j.

* Docker network --internal; absolutely no published ports or host mounts.
* All test data and a random admin password are ephemeral.
* Disposes container + network unconditionally; never contacts production.
* Prints aggregate scenarios and statuses, never raw IDs or auth values.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import argparse
import importlib.util
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import time

from neo4j import GraphDatabase

# New disposable schemas emit harmless missing-label warnings; do not dump
# query bodies or transient database context into operator logs.
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/assistx/endpoint_identity_candidate.py"
spec = importlib.util.spec_from_file_location("endpoint_identity_candidate", SOURCE)
assert spec and spec.loader
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)

NAME = "assistx-canonical-identity-synthetic-20261011"
NET = NAME + "-net"
PREFIX = "scratch-acceptance-" + secrets.token_hex(5)
PASSWORD = secrets.token_urlsafe(24)


def run(argv, **kwargs):
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=kwargs.pop("timeout", 16),
        **kwargs,
    )


def assert_ok(result, note):
    if result.returncode != 0:
        raise RuntimeError(note)


def capture_status(stats):
    items = list(stats.items())
    if any(not isinstance(value, bool) for _, value in items):
        raise RuntimeError("nonboolean acceptance evidence")
    return dict(items)


def count(session, statement, **params):
    record = session.run(statement, **params).single()
    if record is None:
        raise RuntimeError("count evidence unavailable")
    return int(record["n"])


def scenario(driver, results):
    def own(kind):
        return PREFIX + ":" + kind

    with driver.session(database="neo4j") as session:
        try:
            candidate.candidate_register_model_endpoint(
                session, endpoint_id=own("pre-schema"),
                node_id=own("owner-a"),
            )
            results["missing_uniqueness_blocks_any_writer"] = False
        except candidate.EndpointIdentityHold:
            results["missing_uniqueness_blocks_any_writer"] = count(
                session, "MATCH (e:ServiceEndpoint) RETURN count(e) AS n",
            ) == 0

        session.run("""
        CREATE CONSTRAINT canonical_service_endpoint IF NOT EXISTS
        FOR (e:ServiceEndpoint) REQUIRE e.endpoint_id IS UNIQUE
        """).consume()
        constraint = session.run("""
        SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties
        WHERE type='UNIQUENESS' AND labelsOrTypes=['ServiceEndpoint']
          AND properties=['endpoint_id']
        RETURN count(*) AS n
        """).single()
        results["isolated_uniqueness_constraint_present"] = (
            constraint is not None and constraint["n"] == 1
        )

        model_first_id = own("model-first")
        owner = own("owner-a")
        first = candidate.candidate_register_model_endpoint(
            session, endpoint_id=model_first_id, node_id=owner,
            safe_properties={"provider": "synthetic"},
            model_ids=(own("model-1"),),
        )
        second = candidate.candidate_register_model_endpoint(
            session, endpoint_id=model_first_id, node_id=owner,
            safe_properties={"provider": "synthetic"},
            model_ids=(own("model-1"),),
        )
        results["model_first_idempotent_no_duplicate"] = (
            first["production_release_authorized"] is False
            and second["production_release_authorized"] is False
            and count(
                session, """
                MATCH (e:ServiceEndpoint {endpoint_id:$endpoint_id})
                RETURN count(e) AS n
                """, endpoint_id=model_first_id,
            ) == 1
            and count(
                session, """
                MATCH (e:ServiceEndpoint:ModelEndpoint {endpoint_id:$endpoint_id})
                RETURN count(e) AS n
                """, endpoint_id=model_first_id,
            ) == 1
            and count(
                session, """
                MATCH (:SwarmNode {node_id:$node_id})-[:EXPOSES]->
                      (e:ServiceEndpoint {endpoint_id:$endpoint_id})-[:SERVES]->
                      (:Model {model_id:$model_id})
                RETURN count(*) AS n
                """,
                node_id=owner, endpoint_id=model_first_id,
                model_id=own("model-1"),
            ) == 1
        )

        service_first_id = own("service-first")
        session.run("""
        MERGE (n:SwarmNode {node_id:$node_id})
        MERGE (e:ServiceEndpoint {endpoint_id:$endpoint_id})
        SET e.service_type='model_endpoint', e.node_id=$node_id
        MERGE (n)-[:EXPOSES]->(e)
        """, node_id=owner, endpoint_id=service_first_id).consume()
        before_links = count(session, """
            MATCH (:SwarmNode {node_id:$node_id})-[:EXPOSES]->
                  (:ServiceEndpoint {endpoint_id:$endpoint_id})
            RETURN count(*) AS n
        """, node_id=owner, endpoint_id=service_first_id)
        candidate.candidate_register_model_endpoint(
            session, endpoint_id=service_first_id,
            node_id=owner, model_ids=(own("model-2"),),
        )
        after_links = count(session, """
            MATCH (:SwarmNode {node_id:$node_id})-[:EXPOSES]->
                  (:ServiceEndpoint {endpoint_id:$endpoint_id})
            RETURN count(*) AS n
        """, node_id=owner, endpoint_id=service_first_id)
        results["service_first_compatible_identity_promotes"] = (
            before_links == after_links == 1
            and count(session, """
                MATCH (e:ServiceEndpoint:ModelEndpoint {endpoint_id:$endpoint_id})
                RETURN count(e) AS n
            """, endpoint_id=service_first_id) == 1
            and count(session, """
                MATCH (:ModelEndpoint {endpoint_id:$endpoint_id})-[:SERVES]->(:Model)
                RETURN count(*) AS n
            """, endpoint_id=service_first_id) == 1
        )

        generic_id = own("ordinary-service")
        session.run("""
            MERGE (n:SwarmNode {node_id:$node_id})
            MERGE (e:ServiceEndpoint {endpoint_id:$endpoint_id})
            SET e.service_type='generic', e.node_id=$node_id
            MERGE (n)-[:EXPOSES]->(e)
        """, node_id=owner, endpoint_id=generic_id).consume()
        try:
            candidate.candidate_register_model_endpoint(
                session, endpoint_id=generic_id, node_id=owner,
            )
            results["ordinary_service_retype_denied"] = False
        except candidate.EndpointIdentityHold:
            results["ordinary_service_retype_denied"] = count(
                session, """
                MATCH (e:ServiceEndpoint:ModelEndpoint {endpoint_id:$endpoint_id})
                RETURN count(e) AS n
                """, endpoint_id=generic_id,
            ) == 0

        foreign_id = own("different-owner")
        session.run("""
            MERGE (n:SwarmNode {node_id:$node_id})
            MERGE (e:ServiceEndpoint {endpoint_id:$endpoint_id})
            SET e.service_type='model_endpoint', e.node_id=$node_id
            MERGE (n)-[:EXPOSES]->(e)
        """, node_id=own("owner-b"), endpoint_id=foreign_id).consume()
        try:
            candidate.candidate_register_model_endpoint(
                session, endpoint_id=foreign_id, node_id=owner,
            )
            results["cross_owner_collision_denied"] = False
        except candidate.EndpointIdentityHold:
            results["cross_owner_collision_denied"] = count(session, """
                MATCH (:SwarmNode {node_id:$node_id})-[:EXPOSES]->
                      (:ServiceEndpoint {endpoint_id:$endpoint_id})
                RETURN count(*) AS n
            """, node_id=owner, endpoint_id=foreign_id) == 0

    race_id = own("concurrent-registration")
    def worker(worker_id):
        with driver.session(database="neo4j") as concurrent:
            try:
                candidate.candidate_register_model_endpoint(
                    concurrent, endpoint_id=race_id, node_id=owner,
                    safe_properties={"provider": "synthetic"},
                    model_ids=(own("concurrent-model"),),
                )
                return "COMPLETED"
            except candidate.EndpointIdentityHold:
                return "HOLD"
            except Exception:
                return "REJECTED_OTHER"
    with ThreadPoolExecutor(max_workers=2) as executor:
        race_statuses = list(executor.map(worker, (1, 2)))
    with driver.session(database="neo4j") as session:
        count_nodes = count(session, """
            MATCH (e:ServiceEndpoint {endpoint_id:$endpoint_id})
            RETURN count(e) AS n
        """, endpoint_id=race_id)
        count_owner = count(session, """
            MATCH (:SwarmNode {node_id:$node_id})-[:EXPOSES]->
                  (:ServiceEndpoint {endpoint_id:$endpoint_id})
            RETURN count(*) AS n
        """, node_id=owner, endpoint_id=race_id)
        results["concurrent_same_owner_no_duplicate_or_edge_fanout"] = (
            count_nodes == 1 and count_owner == 1
            and "COMPLETED" in race_statuses
        )
        results["concurrency_admission_did_not_unsafe_accept_conflict"] = (
            all(x in ("COMPLETED", "HOLD", "REJECTED_OTHER") for x in race_statuses)
        )

    # Adversarial race: two DIFFERENT owners saw an initially empty identity.
    # Synchronize both pre-lock reads, then let the database lock serialize
    # registration. The second writer must re-check ownership under that lock.
    from threading import Barrier, Lock
    foreign_race_id = own("two-owners-one-endpoint")
    barrier = Barrier(2)
    lock = Lock()
    reads = 0
    original_preflight = candidate._validate_preconditions

    def deterministic_race_preflight(tx, endpoint_id, node_id):
        nonlocal reads
        original_preflight(tx, endpoint_id, node_id)
        if endpoint_id == foreign_race_id:
            with lock:
                reads += 1
                initial_read = reads <= 2
            if initial_read:
                barrier.wait(timeout=12)

    def conflicting_worker(worker_node):
        with driver.session(database="neo4j") as concurrent:
            try:
                candidate.candidate_register_model_endpoint(
                    concurrent, endpoint_id=foreign_race_id, node_id=worker_node,
                )
                return "COMPLETED"
            except candidate.EndpointIdentityHold:
                return "HOLD"
            except Exception:
                return "REJECTED_OTHER"

    candidate._validate_preconditions = deterministic_race_preflight
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            conflicting_statuses = list(executor.map(
                conflicting_worker, (own("owner-a"), own("owner-b")),
            ))
    finally:
        candidate._validate_preconditions = original_preflight

    with driver.session(database="neo4j") as session:
        final_state = list(session.run("""
            MATCH (e:ServiceEndpoint {endpoint_id:$endpoint_id})
            OPTIONAL MATCH (o:SwarmNode)-[:EXPOSES]->(e)
            RETURN elementId(e) AS ref, e.node_id AS owner,
                   collect(DISTINCT o.node_id) AS graph_owners
        """, endpoint_id=foreign_race_id))
        results["concurrent_distinct_owners_fail_closed_after_lock"] = (
            conflicting_statuses.count("COMPLETED") == 1
            and conflicting_statuses.count("HOLD") == 1
            and len(final_state) == 1
            and len(final_state[0]["graph_owners"]) == 1
            and final_state[0]["owner"] == final_state[0]["graph_owners"][0]
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-disposable",
        action="store_true",
        help="Explicitly authorize ONLY a throwaway Docker graph and network",
    )
    args = parser.parse_args()
    if not args.run_disposable:
        print(json.dumps({
            "schema": "assistx.canonical-identity-disposable-candidate.v1",
            "status": "HOLD_EXPLICIT_DISPOSABLE_OPT_IN_REQUIRED",
            "docker_commands_executed": False,
            "production_release_authorized": False,
        }, sort_keys=True))
        return 2
    report = {
        "schema": "assistx.canonical-identity-disposable-candidate.v1",
        "status": "HOLD_INCOMPLETE_SYNTHETIC_ACCEPTANCE",
        "source_unwired_from_production": True,
        "network_internal_only": False,
        "published_host_ports": 0,
        "production_volumes_mounted": False,
        "production_graph_mutated": False,
        "credentials_logged": False,
        "production_release_authorized": False,
        "scenarios": {},
        "cleanup": {},
    }
    started = False
    created_net = False
    network_meta = None
    try:
        if run(["docker", "inspect", NAME]).returncode == 0:
            raise RuntimeError("refusing existing container")
        if run(["docker", "network", "inspect", NET]).returncode == 0:
            raise RuntimeError("refusing existing network")
        assert_ok(run(["docker", "network", "create", "--internal", NET]), "network create failed")
        created_net = True
        env = dict(os.environ, NEO4J_AUTH="neo4j/" + PASSWORD)
        result = run([
            "docker", "run", "-d", "--rm", "--name", NAME,
            "--network", NET, "--memory", "2g", "--cpus", "1",
            "--pids-limit", "300", "--security-opt", "no-new-privileges",
            "--env", "NEO4J_AUTH", "--env", "NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
            "--env", "NEO4J_server_memory_heap_initial__size=256m",
            "--env", "NEO4J_server_memory_heap_max__size=512m",
            "--env", "NEO4J_server_memory_pagecache_size=256m",
            "neo4j:5.26-enterprise",
        ], env=env)
        assert_ok(result, "disposable Neo4j startup failed")
        started = True

        container_meta = json.loads(run(["docker", "inspect", NAME]).stdout)[0]
        network_meta = json.loads(run(["docker", "network", "inspect", NET]).stdout)[0]
        if (
            container_meta["HostConfig"]["NetworkMode"] != NET
            or container_meta["HostConfig"].get("PortBindings")
            or network_meta.get("Internal") is not True
            or any(m.get("Type") != "volume" for m in container_meta.get("Mounts", []))
        ):
            raise RuntimeError("test isolation cannot be proved")
        report["network_internal_only"] = True
        network_addresses = list(
            (container_meta["NetworkSettings"].get("Networks") or {}).values()
        )
        if len(network_addresses) != 1 or not network_addresses[0].get("IPAddress"):
            raise RuntimeError("disposable graph address unavailable")
        host = network_addresses[0]["IPAddress"]
        with GraphDatabase.driver(
            f"bolt://{host}:7687",
            auth=("neo4j", PASSWORD),
            connection_timeout=4,
        ) as driver:
            ready = False
            for _ in range(20):
                try:
                    driver.verify_connectivity()
                    ready = True
                    break
                except Exception:
                    time.sleep(2)
            if not ready:
                raise RuntimeError("disposable graph never became ready")
            scenario(driver, report["scenarios"])

        report["status"] = (
            "PASS_DISPOSABLE_UNWIRED_CANDIDATE_ONLY"
            if report["scenarios"] and all(report["scenarios"].values())
            else "HOLD_SYNTHETIC_SCENARIOS_FAILED"
        )
    except Exception as exc:
        report["status"] = "HOLD_SYNTHETIC_HARNESS_UNAVAILABLE"
        report["error_type"] = type(exc).__name__
    finally:
        if started:
            gone = run(["docker", "rm", "-f", "-v", NAME], timeout=14)
            report["cleanup"]["container_removed"] = (
                gone.returncode == 0 and run(["docker", "inspect", NAME]).returncode != 0
            )
        if created_net:
            gone = run(["docker", "network", "rm", NET], timeout=12)
            report["cleanup"]["network_removed"] = (
                gone.returncode == 0
                and run(["docker", "network", "inspect", NET]).returncode != 0
            )
        if started and not report["cleanup"].get("container_removed"):
            report["status"] = "HOLD_INCOMPLETE_CLEANUP"
        if created_net and not report["cleanup"].get("network_removed"):
            report["status"] = "HOLD_INCOMPLETE_CLEANUP"
        print(json.dumps(report, sort_keys=True))
    return (
        0 if report["status"] == "PASS_DISPOSABLE_UNWIRED_CANDIDATE_ONLY"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
