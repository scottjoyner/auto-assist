"""Manual disposable Neo4j role/broker/worker network isolation canary.

Requires ASSISTX_DISPOSABLE_GRAPH_BROKER=1; only uses cached images and
ephemeral Docker networks. NEVER attaches to live neo4j/AssistX containers,
persistent volumes, host ports, system network/firewall, Tailscale or secrets.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

GRAPH_IMAGE = "neo4j:5.26-enterprise"
PYTHON_IMAGE = "python:3.12-slim"
SOURCE = Path(__file__).resolve().parent


def docker(*args: str, timeout: int = 25, check: bool = True):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True,
        timeout=timeout, check=check,
    )


def assert_disposable(container: str, expected_networks: set[str]):
    info = json.loads(docker("inspect", container).stdout)[0]
    networks = set(info["NetworkSettings"]["Networks"])
    host = info["HostConfig"]
    if (networks != expected_networks
            or host.get("PortBindings") or host.get("Binds")
            or any(m["Type"] == "bind" for m in info.get("Mounts", []))):
        raise RuntimeError("UNSAFE_DISPOSABLE_FIXTURE")
    return info


def query(graph: str, admin: str, statement: str,
          user: str = "neo4j", timeout: int = 15):
    # Secrets are random disposable credentials; never print argv/logs.
    proc = docker(
        "exec", graph, "cypher-shell", "-a", "bolt://localhost:7687",
        "-u", user, "-p", admin, "--non-interactive", statement,
        timeout=timeout, check=False,
    )
    return proc


def run() -> dict:
    if os.getenv("ASSISTX_DISPOSABLE_GRAPH_BROKER") != "1":
        raise RuntimeError("EXPLICIT_DISPOSABLE_GRAPH_BROKER_OPT_IN_REQUIRED")
    tag = secrets.token_hex(4)
    worker_net = "assistx-broker-worker-" + tag
    graph_net = "assistx-broker-graph-" + tag
    graph = "assistx-broker-neo-" + tag
    broker = "assistx-broker-gateway-" + tag
    worker = "assistx-broker-worker-" + tag
    admin = "A" + secrets.token_urlsafe(25)
    reader_password = "R" + secrets.token_urlsafe(25)
    bearer = secrets.token_urlsafe(35)
    reader_user = "fixture_reader"
    task_id = "fixture-task-" + tag
    made_networks = []
    made_containers = []
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="assistx-isolated-broker-") as temp:
        tmp = Path(temp)
        try:
            for network in (graph_net, worker_net):
                docker("network", "create", "--internal", "--driver", "bridge",
                       "--label", "assistx.trace.research=task-broker",
                       network)
                made_networks.append(network)

            docker("run", "-d", "--pull", "never", "--name", graph,
                   "--network", graph_net, "--network-alias", "neo4j",
                   "--memory", "2300m", "--cpus", "1.5",
                   "--pids-limit", "256",
                   "--env", "NEO4J_AUTH=neo4j/" + admin,
                   "--env", "NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
                   "--env", "NEO4J_server_memory_heap_initial__size=256m",
                   "--env", "NEO4J_server_memory_heap_max__size=512m",
                   GRAPH_IMAGE, timeout=28)
            made_containers.append(graph)
            assert_disposable(graph, {graph_net})
            info = json.loads(docker("inspect", graph).stdout)[0]
            neo_ip = info["NetworkSettings"]["Networks"][graph_net]["IPAddress"]
            if not neo_ip:
                raise RuntimeError("MISSING_DISPOSABLE_GRAPH_IP")

            deadline = time.monotonic() + 90
            while True:
                status = query(graph, admin, "RETURN 1 AS ok", timeout=10)
                if status.returncode == 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("NEO4J_DISPOSABLE_NOT_READY")
                time.sleep(2)

            role = "fixture_task_reader"
            statements = [
                "CREATE ROLE " + role + " IF NOT EXISTS",
                "GRANT ACCESS ON DATABASE neo4j TO " + role,
                "GRANT MATCH {id, status} ON GRAPH neo4j NODES Task TO " + role,
                "CREATE USER " + reader_user + " SET PASSWORD '" +
                reader_password + "' CHANGE NOT REQUIRED",
                "GRANT ROLE " + role + " TO " + reader_user,
                "CREATE (:Task {id: '" + task_id +
                "', status: 'READY'})",
            ]
            for step, cypher in enumerate(statements, 1):
                check = query(graph, admin, cypher, timeout=15)
                if check.returncode != 0:
                    # Cypher text/arguments may contain credentials, so never
                    # print them or the server's unredacted exception body.
                    raise RuntimeError(
                        "DISPOSABLE_NEO4J_ROLE_OR_SEED_STEP_FAILED:" + str(step)
                    )

            valid_read = query(
                graph, reader_password,
                "MATCH (t:Task {id:'" + task_id +
                "'}) RETURN t.id AS id, t.status AS status",
                user=reader_user)
            if valid_read.returncode != 0 or task_id not in valid_read.stdout:
                raise RuntimeError("READ_ONLY_ROLE_CANNOT_READ_TASK")
            write_attempt = query(
                graph, reader_password,
                "CREATE (:Task {id:'fixture-task-write-denied',status:'READY'})",
                user=reader_user)
            if write_attempt.returncode == 0:
                raise RuntimeError("READ_ONLY_ROLE_WAS_ALLOWED_TO_WRITE")

            # Broker is the only component on BOTH bridges.
            docker("create", "--pull", "never", "--name", broker,
                   "--network", worker_net, "--memory", "180m", "--cpus", "1",
                   "--pids-limit", "96", PYTHON_IMAGE,
                   "python", "-u", "/tmp/task-broker.py", timeout=18)
            made_containers.append(broker)
            docker("network", "connect", graph_net, broker)
            config = {
                "reader_user": reader_user,
                "reader_password": reader_password,
                "bearer": bearer,
            }
            (tmp / "broker-config.json").write_text(
                json.dumps(config), encoding="utf8")
            docker("cp", str(SOURCE / "trace_disposable_task_broker.py"),
                   broker + ":/tmp/task-broker.py")
            docker("cp", str(tmp / "broker-config.json"),
                   broker + ":/tmp/broker-config.json")
            docker("start", broker)
            assert_disposable(broker, {graph_net, worker_net})

            docker("create", "--pull", "never", "--name", worker,
                   "--network", worker_net, "--memory", "128m",
                   "--cpus", "1", "--pids-limit", "96",
                   PYTHON_IMAGE, "sleep", "90", timeout=18)
            made_containers.append(worker)
            (tmp / "worker-config.json").write_text(
                json.dumps({
                    "task_id": task_id, "bearer": bearer,
                    "broker_host": broker, "neo4j_ip": neo_ip,
                }), encoding="utf8")
            docker("cp", str(SOURCE / "trace_disposable_task_worker.py"),
                   worker + ":/tmp/task-worker.py")
            docker("cp", str(SOURCE / "trace_task_read_broker_client_research.py"),
                   worker + ":/tmp/trace_task_read_broker_client_research.py")
            docker("cp", str(tmp / "worker-config.json"),
                   worker + ":/tmp/worker-config.json")
            docker("start", worker)
            assert_disposable(worker, {worker_net})

            # The worker cannot reach Bolt, including by literal graph IP,
            # but can do its fixed lookup through the broker.
            deadline = time.monotonic() + 20
            result = None
            while time.monotonic() < deadline:
                attempt = docker("exec", worker, "python",
                                 "/tmp/task-worker.py",
                                 timeout=12, check=False)
                if attempt.returncode == 0:
                    result = json.loads(attempt.stdout.strip())
                    break
                time.sleep(1)
            if result is None:
                raise RuntimeError("WORKER_TO_BROKER_TASK_READ_ACCEPTANCE_FAILED")
            return {
                **result,
                "schema": "assistx-disposable-task-broker-20261011-v1",
                "runtime_seconds": round(time.monotonic() - started, 2),
                "source_task_method": "Neo4jClient.get_task",
                "tested_subset": ["Task.id", "Task.status"],
                "neo4j_actual_read": True,
                "neo4j_reader_role_read": True,
                "neo4j_reader_role_write_denied": True,
                "graph_host_ports": 0,
                "worker_graph_network_membership": False,
                "broker_dual_network_membership": True,
                "production_neo4j_touched": False,
                "Raft_issuer_required_at_broker": False,
                "production_authority": False,
            }
        finally:
            # Only own unique, labeled, transient names. Never stop live graph.
            for name in reversed(made_containers):
                docker("rm", "-f", "-v", name, check=False, timeout=22)
            for name in reversed(made_networks):
                docker("network", "rm", name, check=False, timeout=20)


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
