"""Disposable PostgreSQL17 -> guarded gateway -> Neo4j 5.26 physical smoke.

RESEARCH ONLY. Single primary, fabricated fixture term 1 (NOT quorum-fenced).
The host test harness controls Docker; this does not prove independent custody.
No production services, host ports, bind mounts or persisted Neo4j content.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from uuid import uuid4

from neo4j import GraphDatabase, READ_ACCESS
from trace_graph_entry_guarded import ProtectedGraphEntry, QueryPlan
from trace_graph_entry_metadata import gateway_metadata
from trace_graph_entry_research import AdmissionDenied, Grant
from trace_graph_entry_research_journal import SqliteResearchJournal


def docker(*args, input=None, timeout=20, check=True):
    try:
        return subprocess.run(["docker", *args], input=input, capture_output=True,
                              text=True, check=check, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # Exception command arguments may include ephemeral passwords.
        raise RuntimeError("DISPOSABLE_DOCKER_COMMAND_FAILED") from None


def run():
    if os.environ.get("ASSISTX_ENTRY_PG_NEO_RESEARCH") != "1":
        raise RuntimeError("EXPLICIT_PG_NEO_RESEARCH_OPT_IN_REQUIRED")
    ident = secrets.token_hex(4)
    network = "assistx-entry-pgneo-net-" + ident
    pg = "assistx-entry-pg17-" + ident
    neo = "assistx-entry-neo526-" + ident
    containers = []
    network_created = False
    admin_pw = secrets.token_hex(20)
    worker_pw = secrets.token_hex(20)
    verifier_pw = secrets.token_hex(20)
    epoch = str(uuid4())

    def psql(role, password, command):
        return docker("exec", "-i", "-e", "PGPASSWORD=" + password, pg,
                      "psql", "-X", "-v", "ON_ERROR_STOP=1", "-t", "-A",
                      "-h", "127.0.0.1", "-d", "postgres", "-U", role,
                      input=command, timeout=14).stdout.strip()

    def fixture_ip(container):
        o = json.loads(docker("inspect", container).stdout)[0]
        h = o["HostConfig"]
        if (h.get("PortBindings") or h.get("Binds") or h.get("Mounts")
                or h["NetworkMode"] != network
                or set(o["NetworkSettings"]["Networks"]) != {network}
                or any(m["Type"] == "bind" for m in o.get("Mounts", []))):
            raise RuntimeError("UNSAFE_PHYSICAL_FIXTURE")
        return o["NetworkSettings"]["Networks"][network]["IPAddress"]

    try:
        docker("network", "create", "--internal", "--driver", "bridge",
               "--label", "assistx.trace.research=pgneo-entry", network)
        network_created = True
        docker("run", "-d", "--name", pg, "--pull", "never", "--network",
               network, "--memory", "512m", "--cpus", "1",
               "--env", "POSTGRES_PASSWORD=" + admin_pw,
               "--env", "POSTGRES_HOST_AUTH_METHOD=scram-sha-256",
               "postgres:17-alpine", timeout=25)
        containers.append(pg)
        docker("run", "-d", "--name", neo, "--pull", "never",
               "--network", network, "--memory", "2200m", "--cpus", "1.5",
               "--pids-limit", "256", "--env", "NEO4J_AUTH=none",
               "--env", "NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
               "--env", "NEO4J_server_memory_heap_initial__size=256m",
               "--env", "NEO4J_server_memory_heap_max__size=512m",
               "neo4j:5.26-enterprise", timeout=25)
        containers.append(neo)
        pg_ip, neo_ip = fixture_ip(pg), fixture_ip(neo)
        if not pg_ip or not neo_ip:
            raise RuntimeError("MISSING_DISPOSABLE_DATABASE_ADDRESS")
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline:
            ready = docker("exec", pg, "pg_isready", "-q", "-U", "postgres",
                           timeout=5, check=False)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("PG_FIXTURE_NOT_READY")
        psql("postgres", admin_pw,
             "CREATE ROLE assistx_trace_research_worker LOGIN PASSWORD '"
             + worker_pw + "';\n"
             "CREATE ROLE assistx_trace_research_verifier LOGIN PASSWORD '"
             + verifier_pw + "';\n")
        schema = (Path(__file__).resolve().parents[1] / "research" /
                  "trace_pg_privilege_fence.sql").read_text()
        psql("postgres", admin_pw, schema)
        psql("postgres", admin_pw,
             "INSERT INTO assistx_trace_fence_research.authority "
             "VALUES(true,'" + epoch + "',1,'assistx-pg-privilege-v1');\n"
             "GRANT USAGE ON SCHEMA assistx_trace_fence_research TO "
             "assistx_trace_research_worker,assistx_trace_research_verifier;\n"
             "GRANT EXECUTE ON FUNCTION "
             "assistx_trace_fence_research.admit(text,text,text), "
             "assistx_trace_fence_research.inspect(text) "
             "TO assistx_trace_research_worker;\n"
             "GRANT EXECUTE ON FUNCTION "
             "assistx_trace_fence_research.release_exact(text,text,text) "
             "TO assistx_trace_research_verifier;\n")

        class Authority:
            def admit(self, operation_id, plan_id):
                token = secrets.token_hex(16)
                out = psql("assistx_trace_research_worker", worker_pw,
                           "SELECT accepted::text || '|' || occupancy::text "
                           "|| '|' || reason FROM "
                           "assistx_trace_fence_research.admit('"
                           + epoch + "','" + token + "','" + operation_id + "');")
                if out.startswith("false|"):
                    return None
                if not out.startswith("true|"):
                    raise RuntimeError("UNEXPECTED_PG_ADMISSION")
                # Term is an explicit FIXTURE PLACEHOLDER, not issued by PG.
                return Grant(operation_id, token, 1, epoch)

        class Graph:
            def __init__(self, driver):
                self.driver = driver
                self.executions = 0

            def execute(self, attempt, cypher, parameters):
                self.executions += 1
                with self.driver.session(
                        database="neo4j", default_access_mode=READ_ACCESS) as session:
                    with session.begin_transaction(
                            metadata=gateway_metadata(attempt, "fixture_read"),
                            timeout=4) as tx:
                        return [record.data() for record in
                                tx.run(cypher, **dict(parameters))]

        uri = "bolt://" + neo_ip + ":7687"
        with GraphDatabase.driver(uri, auth=None, connection_timeout=3) as driver:
            driver.verify_connectivity()
            graph = Graph(driver)
            with tempfile.TemporaryDirectory(prefix="assistx-entry-audit-") as folder:
                ledger = SqliteResearchJournal(Path(folder) / "evidence.sqlite")
                gateway = ProtectedGraphEntry(
                    {"fixture_read": QueryPlan(
                        "RETURN $value AS value", ("value",))},
                    Authority(), ledger, graph, epoch)
                try:
                    gateway.execute("UNMARKED_BYPASS", {"value": 99})
                except AdmissionDenied as exc:
                    assert str(exc) == "UNREGISTERED_QUERY_PLAN"
                else:
                    raise AssertionError("ARBITRARY_QUERY_BYPASS")
                outcome = gateway.execute("fixture_read", {"value": 42})
                if outcome != [{"value": 42}]:
                    raise AssertionError("APPROVED_GRAPH_READ_FAILED")
                if graph.executions != 1:
                    raise AssertionError("UNEXPECTED_GRAPH_EXECUTIONS")
                try:
                    gateway.execute("fixture_read", {"value": 43})
                except AdmissionDenied as exc:
                    assert str(exc) == "CAPACITY_DENIED"
                else:
                    raise AssertionError("OVERADMISSION_AFTER_GRAPH_CALL")
                assert graph.executions == 1
                active = psql("assistx_trace_research_worker", worker_pw,
                              "SELECT assistx_trace_fence_research.inspect('"
                              + epoch + "');")
                assert active == "1", active
                unauthorized = docker(
                    "exec", "-i", "-e", "PGPASSWORD=" + worker_pw, pg,
                    "psql", "-X", "-v", "ON_ERROR_STOP=1", "-t", "-A",
                    "-h", "127.0.0.1", "-d", "postgres",
                    "-U", "assistx_trace_research_worker",
                    input="SELECT assistx_trace_fence_research.release_exact('"
                          + epoch + "','00000000000000000000000000000000','x');",
                    timeout=12, check=False)
                assert unauthorized.returncode != 0
                assert ledger.verify_chain()[0] == 3
        return {
            "schema": "assistx-real-pg-neo-entry-smoke-v1",
            "pg17_restricted_admission": True,
            "neo526_gateway_read": True,
            "unregistered_query_rejected": True,
            "successor_denied_while_capacity_held": True,
            "worker_pg_release_denied": True,
            "durable_local_events": 3,
            "production_authority": False,
            "neo4j_server_enforced_admission": False,
            "global_quorum_failover_proven": False,
            "fixture_term_is_monotonic": False,
            "independent_closure_integrated": False,
        }
    finally:
        for container in reversed(containers):
            docker("rm", "-f", "-v", container, timeout=25, check=False)
        if network_created:
            docker("network", "rm", network, timeout=25, check=False)


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
