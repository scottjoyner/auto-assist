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
import threading
import time

GRAPH_IMAGE = "neo4j:5.26-enterprise"
PYTHON_IMAGE = "python:3.12-slim"
SOURCE = Path(__file__).resolve().parent


def docker(*args: str, timeout: int = 25, check: bool = True):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True,
        timeout=timeout, check=check,
    )


def assert_disposable(container: str, expected_networks: set[str],
                      allowed_policy_mount: str | None = None):
    info = json.loads(docker("inspect", container).stdout)[0]
    networks = set(info["NetworkSettings"]["Networks"])
    host = info["HostConfig"]
    mounts = [m for m in info.get("Mounts", []) if m["Type"] == "bind"]
    expected_mount = []
    if allowed_policy_mount is not None:
        expected_mount = [(
            allowed_policy_mount, "/run/assistx-policy"
        )]
    actual_mount = [
        (m["Source"], m["Destination"]) for m in mounts
    ]
    if (networks != expected_networks
            or host.get("PortBindings")
            or actual_mount != expected_mount
            or any(not m.get("RW", True) is False for m in mounts)):
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


def run(raft_authority=None) -> dict:
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
    critical_id = "fixture-task-" + tag + "-crit"
    recovery_id = "fixture-task-" + tag + "-recov"
    payload_recovery_id = "fixture-task-" + tag + "-payrec"
    made_networks = []
    made_containers = []
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="assistx-isolated-broker-") as temp:
        tmp = Path(temp)
        policy_service = None
        policy_thread = None
        raft_fence = None
        policy_socket_dir = None
        try:
            if raft_authority is not None:
                # This requires an externally created three-voter disposable
                # etcd cluster; never a configured production authority.
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                from trace_etcd_quorum_fence_research import EtcdQuorumFence
                from trace_etcd_policy_writer_research import (
                    PolicyEngine, UnixPolicyWriterServer
                )
                etcd_client, cluster_id, keyspace = raft_authority
                raft_fence = EtcdQuorumFence(etcd_client, keyspace, cluster_id)
                witness = Ed25519PrivateKey.generate()
                operator = Ed25519PrivateKey.generate()
                pub = lambda k: k.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                raft_fence.bootstrap(
                    "task-broker-raft-genesis", "task-broker-owner",
                    pub(operator), pub(witness), capacity=32,
                )
                policy_socket_dir = tmp / "private-policy"
                policy_socket_dir.mkdir(mode=0o700)
                policy_service = UnixPolicyWriterServer(
                    policy_socket_dir / "authority.sock",
                    PolicyEngine(raft_fence, "task-broker-owner", 1,
                                 "task-broker-raft-genesis",
                                 frozenset({"get_task_read",
                                            "get_task_claim_guard"})),
                    allowed_uid=0,
                )
                policy_thread = threading.Thread(
                    target=policy_service.serve_forever, daemon=True)
                policy_thread.start()

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
                "GRANT MATCH {id, status, payload, payload_json, queue_class, "
                "required_capabilities} ON GRAPH neo4j NODES Task TO " + role,
                "CREATE USER " + reader_user + " SET PASSWORD '" +
                reader_password + "' CHANGE NOT REQUIRED",
                "GRANT ROLE " + role + " TO " + reader_user,
                "CREATE (:Task {id: '" + task_id +
                "', status: 'READY'})",
                "CREATE (:Task {id: '" + critical_id +
                "', status: 'READY', queue_class: 'critical', "
                "payload_json: '{\"queue_class\":\"critical\"}', "
                "required_capabilities: []})",
                "CREATE (:Task {id: '" + recovery_id +
                "', status: 'READY', "
                "payload_json: '{\"runbook\":\"fixture-only\"}', "
                "required_capabilities: ['recovery']})",
                "CREATE (:Task {id: '" + payload_recovery_id +
                "', status: 'READY', "
                "payload: '{\"runbook\":\"payload-only\"}', "
                "required_capabilities: []})",
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
            broker_command = ["create", "--pull", "never", "--name", broker,
                              "--network", worker_net, "--memory", "180m",
                              "--cpus", "1", "--pids-limit", "96"]
            if raft_fence is not None:
                broker_command += [
                    "--env", "ASSISTX_DISPOSABLE_REQUIRE_RAFT=1",
                    "--mount", "type=bind,src=" + str(policy_socket_dir) +
                               ",dst=/run/assistx-policy,readonly",
                ]
            docker(*broker_command, PYTHON_IMAGE,
                   "python", "-u", "/tmp/task-broker.py", timeout=18)
            made_containers.append(broker)
            docker("network", "connect", graph_net, broker)
            config = {
                "reader_user": reader_user,
                "reader_password": reader_password,
                "bearer": bearer,
            }
            if raft_fence is not None:
                config.update({
                    "admission_socket": "/run/assistx-policy/authority.sock",
                    "worker_subject": "fixture-worker",
                })
            (tmp / "broker-config.json").write_text(
                json.dumps(config), encoding="utf8")
            docker("cp", str(SOURCE / "trace_disposable_task_broker.py"),
                   broker + ":/tmp/task-broker.py")
            docker("cp", str(tmp / "broker-config.json"),
                   broker + ":/tmp/broker-config.json")
            docker("start", broker)
            assert_disposable(
                broker, {graph_net, worker_net},
                str(policy_socket_dir) if policy_socket_dir else None)

            docker("create", "--pull", "never", "--name", worker,
                   "--network", worker_net, "--memory", "128m",
                   "--cpus", "1", "--pids-limit", "96",
                   PYTHON_IMAGE, "sleep", "90", timeout=18)
            made_containers.append(worker)
            (tmp / "worker-config.json").write_text(
                json.dumps({
                    "task_id": task_id,
                    "critical_task_id": critical_id,
                    "recovery_task_id": recovery_id,
                    "payload_recovery_task_id": payload_recovery_id,
                    "bearer": bearer,
                    "broker_host": broker, "neo4j_ip": neo_ip,
                    "requires_raft": raft_fence is not None,
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
            if raft_fence is not None:
                pending = raft_fence.snapshot().document["pending"]
                assert len(pending) >= 6, "REAL_RAFT_ADMISSION_NOT_COMMITTED"
                # Unbound graph reads remain held; there is NO synthetic
                # witness closure or unsafe automatic capacity release.
                assert all(item["state"] == "RESERVED"
                           for item in pending.values())
                result["real_three_host_raft_grants_committed"] = True
                result["incomplete_closure_capacity_retained"] = True
            return {
                **result,
                "schema": "assistx-disposable-task-broker-20261011-v1",
                "runtime_seconds": round(time.monotonic() - started, 2),
                "source_task_method": "Neo4jClient.get_task",
                "tested_subset": ["Task.id", "Task.status"],
                "claim_guard_fields": [
                    "Task.id", "Task.status", "Task.queue_class",
                    "Task.payload", "Task.payload_json",
                    "Task.required_capabilities",
                ],
                "neo4j_actual_read": True,
                "neo4j_reader_role_read": True,
                "neo4j_reader_role_write_denied": True,
                "graph_host_ports": 0,
                "worker_graph_network_membership": False,
                "broker_dual_network_membership": True,
                "production_neo4j_touched": False,
                "Raft_issuer_required_at_broker": raft_fence is not None,
                "broker_policy_socket_only_when_raft_enabled": True,
                "production_authority": False,
            }
        finally:
            # Only own unique, labeled, transient names. Never stop live graph.
            for name in reversed(made_containers):
                docker("rm", "-f", "-v", name, check=False, timeout=22)
            for name in reversed(made_networks):
                docker("network", "rm", name, check=False, timeout=20)
            if policy_service is not None:
                policy_service.shutdown()
                policy_thread.join(timeout=3)


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
