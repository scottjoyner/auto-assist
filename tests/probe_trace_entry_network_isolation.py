"""Explicitly opted-in, disposable physical Bolt network isolation probe.

Runs only cached Neo4j 5.26 and Alpine images on two internal-only Docker
networks. Never touches production Neo4j, host ports, persistent mounts,
production credentials or external services. Teardown is always attempted.

This is a network REACHABILITY test, NOT PostgreSQL admission or quorum proof.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import time


def docker(*args: str, timeout: int = 20, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=check, capture_output=True,
                          text=True, timeout=timeout)


def run() -> dict:
    if os.environ.get("ASSISTX_ENTRY_NETWORK_RESEARCH") != "1":
        raise RuntimeError("EXPLICIT_NETWORK_RESEARCH_OPT_IN_REQUIRED")
    ident = secrets.token_hex(4)
    gateway_net = "assistx-entry-gateway-" + ident
    worker_net = "assistx-entry-worker-" + ident
    graph = "assistx-entry-neo526-" + ident
    created_nets = []
    started_graph = False
    result = {
        "schema": "assistx-entry-isolation-physical-v1",
        "fixture_only": True, "postgres_admission_proven": False,
        "server_transaction_binding_proven": False,
        "quorum_failover_proven": False,
        "production_authority": False,
    }
    try:
        for network in (gateway_net, worker_net):
            docker("network", "create", "--internal", "--driver", "bridge",
                   "--label", "assistx.trace.research=graph-entry-isolation",
                   network)
            created_nets.append(network)
        docker("run", "-d", "--name", graph, "--pull", "never",
               "--network", gateway_net, "--cpus", "1.5",
               "--memory", "2200m", "--pids-limit", "256",
               "--env", "NEO4J_AUTH=none",
               "--env", "NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
               "--env", "NEO4J_server_memory_heap_initial__size=256m",
               "--env", "NEO4J_server_memory_heap_max__size=512m",
               "neo4j:5.26-enterprise", timeout=25)
        started_graph = True
        details = json.loads(docker("inspect", graph).stdout)[0]
        host = details["HostConfig"]
        if host.get("PortBindings") or host.get("Binds") or host.get("Mounts"):
            raise RuntimeError("UNSAFE_DOCKER_CONFIG")
        if details["HostConfig"]["NetworkMode"] != gateway_net:
            raise RuntimeError("WRONG_GRAPH_NETWORK")
        # Confirm the dynamically allocated graph address only within fixture.
        neo_ip = details["NetworkSettings"]["Networks"][gateway_net]["IPAddress"]
        if not neo_ip:
            raise RuntimeError("GRAPH_FIXTURE_HAS_NO_IP")
        deadline = time.monotonic() + 70
        gateway_reachable = False
        while time.monotonic() < deadline:
            if docker("inspect", "-f", "{{.State.Running}}", graph).stdout.strip() != "true":
                raise RuntimeError("GRAPH_PROCESS_STOPPED")
            test = docker("run", "--rm", "--pull", "never", "--network",
                          gateway_net, "alpine:3.24", "sh", "-c",
                          f"nc -z -w2 {graph} 7687", timeout=12, check=False)
            if test.returncode == 0:
                gateway_reachable = True
                break
            time.sleep(2)
        if not gateway_reachable:
            raise RuntimeError("GATEWAY_BOLT_NOT_REACHABLE")
        # Separate worker has no attached route or credentials. Testing the
        # literal IP matters: DNS failure alone is not proof of isolation.
        unauthorized = docker("run", "--rm", "--pull", "never", "--network",
                              worker_net, "alpine:3.24", "sh", "-c",
                              f"nc -z -w2 {neo_ip} 7687",
                              timeout=15, check=False)
        if unauthorized.returncode == 0:
            raise RuntimeError("WORKER_BOLT_BYPASS_REPRODUCED")
        # An actual protocol query from gateway's trusted network.
        query = docker("run", "--rm", "--pull", "never", "--network", gateway_net,
                       "--entrypoint", "cypher-shell", "neo4j:5.26-enterprise",
                       "-a", f"bolt://{graph}:7687", "--non-interactive",
                       "RETURN 1 AS probe", timeout=30, check=False)
        if query.returncode != 0 or "1" not in query.stdout:
            raise RuntimeError("GATEWAY_CYPHER_QUERY_FAILED: " +
                               query.stderr[-180:].replace("\n", " "))
        # Separately demonstrate Neo4j's server-observed metadata, not a
        # worker-supplied Cypher comment. Host harness is privileged and must
        # never be confused with a sandboxed production worker.
        from neo4j import GraphDatabase
        from trace_graph_entry_research import BoundAttempt, Grant
        from trace_graph_entry_metadata import gateway_metadata, observed_exact
        attempt = BoundAttempt("fixture-op-" + ident, "fixture-attempt-" + ident,
                               Grant("fixture-reservation-" + ident,
                                     "never-send-this-token-to-neo4j",
                                     1, "fixture-epoch-" + ident))
        metadata = gateway_metadata(attempt, "fixture_read")
        with GraphDatabase.driver(f"bolt://{neo_ip}:7687", auth=None,
                                  connection_timeout=3) as client:
            with client.session(database="neo4j") as executing:
                with executing.begin_transaction(metadata=metadata, timeout=12) as tx:
                    tx.run("RETURN 1 AS observed").consume()
                    with client.session(database="system") as observer:
                        rows = [dict(row) for row in observer.run(
                            "SHOW TRANSACTIONS YIELD transactionId, database, metaData "
                            "RETURN transactionId, database, metaData"
                        )]
                    exact = observed_exact(rows, metadata)
                    if not exact:
                        raise RuntimeError("SERVER_GATEWAY_METADATA_BINDING_MISSING")
                    if observed_exact(rows, dict(
                            metadata, assistx_attempt_id="forged-attempt")):
                        raise RuntimeError("FORGED_GATEWAY_ATTEMPT_ACCEPTED")
        result.update({
            "server_observed_gateway_metadata": True,
            "host_harness_can_access_private_neo4j": True,
            "gateway_network_bolt_reachable": True,
            "worker_network_literal_ip_bolt_denied": True,
            "gateway_actual_neo4j_read": True,
            "graph_image": details["Config"]["Image"],
            "graph_id_short": details["Id"][:12],
            "graph_has_published_ports": False,
            "graph_has_host_mounts": False,
        })
        return result
    finally:
        if started_graph:
            docker("rm", "-f", "-v", graph, timeout=25, check=False)
        for network in reversed(created_nets):
            docker("network", "rm", network, timeout=20, check=False)


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
