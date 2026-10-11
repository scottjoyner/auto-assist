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
RAFT_IMAGE = "quay.io/coreos/etcd:v3.6.14"
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


def run(external_writer: dict | None = None) -> dict:
    remote = external_writer is not None
    if remote and (
            os.getenv("ASSISTX_THREE_HOST_RAFT_RESEARCH") != "1" or
            os.getenv("ASSISTX_THREE_HOST_SPLIT_WRITER_RESEARCH") != "1"):
        raise RuntimeError("THREE_HOST_WRITER_REQUIRES_DOUBLE_OPT_IN")
    if os.getenv("ASSISTX_DISPOSABLE_GRAPH_BROKER") != "1":
        raise RuntimeError("EXPLICIT_DISPOSABLE_GRAPH_BROKER_OPT_IN_REQUIRED")
    joined = remote or os.getenv("ASSISTX_DISPOSABLE_RAFT_IN_BROKER") == "1"
    split = remote or os.getenv("ASSISTX_DISPOSABLE_WRITER_SPLIT") == "1"
    b06 = remote and os.getenv("ASSISTX_B06_HELD_BOLT_RESEARCH") == "1"
    if b06 and os.getenv("ASSISTX_B06_GRAPH_RESTART_RESEARCH") == "1":
        raise RuntimeError("B06_HELD_AND_RESTART_MODES_EXCLUSIVE")
    if split and not joined:
        raise RuntimeError("POLICY_WRITER_SPLIT_REQUIRES_JOINED_RAFT")
    tag = secrets.token_hex(4)
    worker_net = "assistx-broker-worker-" + tag
    raft_net = "assistx-broker-raft-" + tag
    writer_net = "assistx-broker-ipc-" + tag
    writer_egress_net = "assistx-broker-outbound-" + tag
    writer = "assistx-broker-policy-" + tag
    writer_token = secrets.token_urlsafe(35)
    raft_aliases = ["assistx-quorum-r" + str(i) + "-" + tag for i in (1,2,3)]
    raft_key = (external_writer["raft_key"] if remote
                else "/assistx/research/fencing/taskbroker-" + tag)
    raft_genesis = (external_writer["raft_genesis"] if remote
                    else "disposable-task-broker-" + tag)
    raft_owner = (external_writer["raft_owner"] if remote
                  else "disposable-broker")
    graph_net = "assistx-broker-graph-" + tag
    graph = "assistx-broker-neo-" + tag
    broker = "assistx-broker-gateway-" + tag
    worker = "assistx-broker-worker-" + tag
    admin = "A" + secrets.token_urlsafe(25)
    reader_password = "R" + secrets.token_urlsafe(25)
    bearer = secrets.token_urlsafe(35)
    reader_user = "fixture_reader"
    observer_user = "fixture_observer"
    observer_password = "O" + secrets.token_urlsafe(25)
    observer = "assistx-broker-observer-" + tag
    task_id = "fixture-task-" + tag
    critical_id = "fixture-task-" + tag + "-crit"
    recovery_id = "fixture-task-" + tag + "-recov"
    payload_recovery_id = "fixture-task-" + tag + "-payrec"
    made_networks = []
    made_containers = []
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="assistx-isolated-broker-") as temp:
        tmp = Path(temp)
        try:
            networks = [graph_net, worker_net]
            if joined and not remote:
                networks.append(raft_net)
            if split:
                networks.append(writer_net)
            if remote:
                networks.append(writer_egress_net)
            for network in networks:
                cmd = ["network", "create"]
                if network != writer_egress_net:
                    cmd.append("--internal")
                cmd += ["--driver", "bridge",
                        "--label", "assistx.trace.research=task-broker",
                        network]
                docker(*cmd)
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
            if b06:
                role_observer = "fixture_tx_observer"
                statements.extend([
                    "CREATE ROLE " + role_observer + " IF NOT EXISTS",
                    "GRANT ACCESS ON DATABASE neo4j TO " + role_observer,
                    "GRANT SHOW TRANSACTION (fixture_reader) ON DATABASE "
                    "neo4j TO " + role_observer,
                    "CREATE USER " + observer_user + " SET PASSWORD '" +
                    observer_password + "' CHANGE NOT REQUIRED",
                    "GRANT ROLE " + role_observer + " TO " + observer_user,
                ])
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

            if joined and not remote:
                initial = ",".join(
                    "r" + str(i) + "=http://" + raft_aliases[i-1] + ":2380"
                    for i in (1,2,3))
                for i, alias in enumerate(raft_aliases, 1):
                    # Three real etcd voters on ONE physical host; not
                    # independent failure domains, TLS or production RBAC.
                    name = "assistx-broker-quorum-r" + str(i) + "-" + tag
                    docker(
                        "run", "-d", "--pull", "never", "--name", name,
                        "--network", raft_net, "--network-alias", alias,
                        "--memory", "250m", "--cpus", "0.5",
                        "--pids-limit", "100",
                        RAFT_IMAGE, "/usr/local/bin/etcd",
                        "--name", "r" + str(i),
                        "--data-dir", "/etcd-data",
                        "--listen-client-urls", "http://0.0.0.0:2379",
                        "--advertise-client-urls", "http://" + alias + ":2379",
                        "--listen-peer-urls", "http://0.0.0.0:2380",
                        "--initial-advertise-peer-urls", "http://" + alias + ":2380",
                        "--initial-cluster", initial,
                        "--initial-cluster-state", "new",
                        "--initial-cluster-token", "disposable-" + tag,
                        "--logger", "zap", "--log-level", "warn",
                        timeout=20)
                    made_containers.append(name)
                    assert_disposable(name, {raft_net})
                init_name = "assistx-broker-raft-init-" + tag
                endpoint = "http://" + raft_aliases[0] + ":2379"
                docker("create", "--pull", "never", "--name", init_name,
                       "--network", raft_net, PYTHON_IMAGE,
                       "python", "/tmp/bootstrap.py", endpoint, raft_key,
                       raft_genesis, raft_owner)
                made_containers.append(init_name)
                for src, dst in (
                    ("trace_disposable_joined_bootstrap.py", "bootstrap.py"),
                    ("trace_disposable_raft_http.py",
                     "trace_disposable_raft_http.py"),
                    ("trace_etcd_quorum_fence_research.py",
                     "trace_etcd_quorum_fence_research.py"),
                ):
                    docker("cp", str(SOURCE / src), init_name + ":/tmp/" + dst)
                ready = docker("start", "-a", init_name, timeout=38)
                cluster = json.loads(ready.stdout.strip())["cluster_id"]
                if not cluster:
                    raise RuntimeError("DISPOSABLE_RAFT_CLUSTER_ID_MISSING")

            if split:
                # Distinct container with no access to graph/worker network.
                docker("create", "--pull", "never", "--name", writer,
                       "--network", writer_net,
                       "--network-alias", writer, "--memory", "170m",
                       "--cpus", "0.7", "--pids-limit", "96",
                       PYTHON_IMAGE,
                       "python", "-u", "/tmp/policy-writer.py", timeout=18)
                made_containers.append(writer)
                if remote:
                    docker("network", "connect", writer_egress_net, writer)
                else:
                    docker("network", "connect", raft_net, writer)
                writer_config = {
                    "writer_token": writer_token,
                    "raft_key": raft_key,
                    "raft_cluster": (external_writer["raft_cluster"]
                                    if remote else cluster),
                    "raft_genesis": raft_genesis,
                    "raft_owner": raft_owner, "raft_term": 1,
                }
                if remote:
                    writer_config.update({
                        "raft_tls_endpoint": external_writer[
                            "raft_tls_endpoint"],
                        "raft_bearer": external_writer["raft_bearer"],
                    })
                else:
                    writer_config["raft_endpoint"] = endpoint
                (tmp / "writer-config.json").write_text(
                    json.dumps(writer_config), encoding="utf8")
                for src, dst in (
                    ("trace_disposable_separate_policy_writer.py",
                     "policy-writer.py"),
                    ("trace_etcd_quorum_fence_research.py",
                     "trace_etcd_quorum_fence_research.py"),
                    ("trace_disposable_raft_http.py",
                     "trace_disposable_raft_http.py"),
                ) + ((("trace_etcd_bearer_rbac_research.py",
                        "trace_etcd_bearer_rbac_research.py"),)
                     if remote else ()):
                    docker("cp", str(SOURCE / src), writer + ":/tmp/" + dst)
                if remote:
                    docker(
                        "cp", external_writer["tls_client_dir"],
                        writer + ":/tmp/etcd-client")
                docker("cp", str(tmp / "writer-config.json"),
                       writer + ":/tmp/writer-config.json")
                docker("start", writer)
                assert_disposable(
                    writer, {writer_net, writer_egress_net}
                    if remote else {writer_net, raft_net})

            # Graph broker has no raw Raft access in split mode.
            docker("create", "--pull", "never", "--name", broker,
                   "--network", worker_net, "--memory", "180m", "--cpus", "1",
                   "--pids-limit", "96", PYTHON_IMAGE,
                   "python", "-u", "/tmp/task-broker.py", timeout=18)
            made_containers.append(broker)
            docker("network", "connect", graph_net, broker)
            if joined and not split:
                docker("network", "connect", raft_net, broker)
            if split:
                docker("network", "connect", writer_net, broker)
            config = {
                "reader_user": reader_user,
                "reader_password": reader_password,
                "bearer": bearer,
            }
            if joined:
                if split:
                    config.update({
                        "writer_endpoint": "http://" + writer + ":8810",
                        "writer_token": writer_token,
                    })
                    broker_modules = (
                        "trace_graph_stable_identity_research.py",
                        "trace_etcd_quorum_fence_research.py",
                    )
                else:
                    config.update({
                        "raft_endpoint": endpoint, "raft_key": raft_key,
                        "raft_cluster": cluster, "raft_genesis": raft_genesis,
                        "raft_owner": raft_owner, "raft_term": 1,
                    })
                    broker_modules = (
                        "trace_etcd_quorum_fence_research.py",
                        "trace_disposable_raft_http.py",
                        "trace_graph_stable_identity_research.py",
                    )
                for src in broker_modules:
                    docker("cp", str(SOURCE / src), broker + ":/tmp/" + src)
                if b06:
                    config.update({"b06_explicit": True,
                                   "b06_genesis": raft_genesis})
                    docker("cp", str(SOURCE / "trace_b06_broker_held_bolt.py"),
                           broker + ":/tmp/trace_b06_broker_held_bolt.py")
                    # Use only the locally installed Python-driver package,
                    # copied into a disposable container with no bind mount.
                    import neo4j
                    package = Path(neo4j.__file__).resolve().parent
                    docker("cp", str(package), broker + ":/tmp/neo4j")
            (tmp / "broker-config.json").write_text(
                json.dumps(config), encoding="utf8")
            docker("cp", str(SOURCE / "trace_disposable_task_broker.py"),
                   broker + ":/tmp/task-broker.py")
            docker("cp", str(tmp / "broker-config.json"),
                   broker + ":/tmp/broker-config.json")
            docker("start", broker)
            if b06:
                docker(
                    "create", "--pull", "never", "--name", observer,
                    "--network", graph_net, "--memory", "128m",
                    "--cpus", "0.7", "--pids-limit", "96",
                    PYTHON_IMAGE, "sleep", "160", timeout=18)
                made_containers.append(observer)
                observer_config = {
                    "observer_user": observer_user,
                    "observer_password": observer_password,
                }
                (tmp / "observer-config.json").write_text(
                    json.dumps(observer_config), encoding="utf8")
                for src in (
                    "trace_b06_observer_disposable.py",
                    "trace_b06_observation_gate_research.py",
                    "trace_graph_entry_metadata.py",
                    "trace_graph_entry_research.py",
                ):
                    docker("cp", str(SOURCE / src),
                           observer + ":/tmp/" + src)
                docker("cp", str(tmp / "observer-config.json"),
                       observer + ":/tmp/observer-config.json")
                docker("start", observer)
                assert_disposable(observer, {graph_net})
                # Observer must not possess Neo4j task-write privileges.
                denied = query(
                    graph, observer_password,
                    "CREATE (:Task {id:'denied-observer-write'})",
                    user=observer_user)
                if denied.returncode == 0:
                    raise RuntimeError("OBSERVER_GRAPH_WRITE_ALLOWED")
            assert_disposable(
                broker,
                {graph_net, worker_net, writer_net} if split else
                {graph_net, worker_net, raft_net} if joined else
                {graph_net, worker_net})

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
                    **({"b06_explicit": True} if b06 else {}),
                    **({"quorum_ip": external_writer["raft_ip"],
                        "quorum_port": external_writer["raft_port"],
                        "remote_three_host": True}
                       if remote else {"quorum_ip": json.loads(
                        docker("inspect", "assistx-broker-quorum-r1-" + tag
                               ).stdout)[0]["NetworkSettings"][
                        "Networks"][raft_net]["IPAddress"],
                        "quorum_port": 2379} if joined else {}),
                    **({"writer_ip": json.loads(
                        docker("inspect", writer).stdout)[0]
                        ["NetworkSettings"]["Networks"][writer_net]
                        ["IPAddress"]} if split else {}),
                }), encoding="utf8")
            docker("cp", str(SOURCE / (
                       "trace_disposable_joined_raft_worker.py" if joined
                       else "trace_disposable_task_worker.py")),
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
            b06_observed = False
            if b06:
                # Two real Raft slots are already occupied. This worker's
                # third operation now goes through the SAME authenticated
                # broker→scoped writer admission and opens a real explicit
                # Bolt transaction in the broker (not in this host).
                docker("cp", str(SOURCE / "trace_b06_worker_held_request.py"),
                       worker + ":/tmp/held-worker.py")
                docker("cp", str(SOURCE / "trace_b06_worker_capacity.py"),
                       worker + ":/tmp/held-capacity.py")
                active_proc = subprocess.Popen(
                    ["docker", "exec", worker, "python",
                     "/tmp/held-worker.py"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 24
                    while True:
                        exists = docker(
                            "exec", broker, "test", "-f",
                            "/tmp/b06-target.json", timeout=6, check=False)
                        if exists.returncode == 0:
                            break
                        if active_proc.poll() is not None:
                            raise RuntimeError("B06_BROKER_HELD_REQUEST_EXITED")
                        if time.monotonic() > deadline:
                            raise RuntimeError("B06_BROKER_BOLT_NOT_HELD")
                        time.sleep(.25)
                    local_target = tmp / "b06-observed-target.json"
                    docker("cp", broker + ":/tmp/b06-target.json",
                           str(local_target))
                    docker("cp", str(local_target),
                           observer + ":/tmp/observer-target.json")
                    active = docker(
                        "exec", observer, "python", "/tmp/" +
                        "trace_b06_observer_disposable.py", "active",
                        timeout=22, check=False)
                    if active.returncode != 0:
                        raise RuntimeError("B06_INDEPENDENT_ACTIVE_NOT_OBSERVED")
                    active_data = json.loads(active.stdout)
                    if (active_data.get("status") !=
                            "EXACT_ACTIVE_TRANSACTION_OBSERVED" or
                            active_data.get("release_authorized") is not False):
                        raise RuntimeError("B06_ACTIVE_OBSERVER_INVALID")
                    docker("exec", broker, "python", "-c",
                           "from pathlib import Path;"
                           "Path('/tmp/b06-release').touch()", timeout=8)
                    worker_output, _ = active_proc.communicate(timeout=17)
                    if active_proc.returncode != 0:
                        raise RuntimeError("B06_HELD_WORKER_TRANSACTION_FAILED")
                    worker_ok = json.loads(worker_output)
                    if worker_ok.get("held_bolt_request_finished") is not True:
                        raise RuntimeError("B06_HELD_WORKER_NOT_COMPLETE")
                    absent = docker(
                        "exec", observer, "python",
                        "/tmp/trace_b06_observer_disposable.py",
                        "absent", timeout=22, check=False)
                    if absent.returncode != 0:
                        raise RuntimeError("B06_OBSERVER_ABSENCE_FAILED")
                    absent_data = json.loads(absent.stdout)
                    if (absent_data.get("status") !=
                            "ABSENT_OBSERVATIONS_ONLY" or
                            absent_data.get("release_authorized") is not False):
                        raise RuntimeError("B06_OBSERVER_RELEASE_ESCALATION")
                    cap = docker(
                        "exec", worker, "python", "/tmp/held-capacity.py",
                        timeout=11)
                    if not json.loads(cap.stdout).get(
                            "fourth_request_denied_after_observed_bolt"):
                        raise RuntimeError("B06_CAPACITY_RELEASED_UNVERIFIED")
                    b06_observed = True
                finally:
                    if active_proc.poll() is None:
                        active_proc.kill()
                        active_proc.communicate(timeout=5)
            if joined:
                verifier = (
                    "import json,sys;sys.path.insert(0,'/tmp');"
                    "from trace_disposable_raft_http import ResearchHTTP;"
                    "from trace_etcd_quorum_fence_research import EtcdQuorumFence;"
                    "p=json.load(open('/tmp/broker-config.json'));"
                    "q=EtcdQuorumFence(ResearchHTTP(p['raft_endpoint']),"
                    "p['raft_key'],pinned_cluster_id=p['raft_cluster']);"
                    "v=q.snapshot();"
                    "print(json.dumps({'pending':len(v.document['pending']),"
                    "'completed':len(v.document.get('completed',{}))}))"
                )
                if split:
                    verifier = verifier.replace(
                        "'/tmp/broker-config.json'",
                        "'/tmp/writer-config.json'")
                if remote:
                    verifier = (
                        "import json,sys;sys.path.insert(0,'/tmp');"
                        "from trace_etcd_bearer_rbac_research import EtcdBearerTLS;"
                        "from trace_etcd_quorum_fence_research import EtcdQuorumFence;"
                        "p=json.load(open('/tmp/writer-config.json'));"
                        "v=EtcdQuorumFence(EtcdBearerTLS("
                        "p['raft_tls_endpoint'],"
                        "'/tmp/etcd-client/ca.pem',"
                        "'/tmp/etcd-client/node.pem',"
                        "'/tmp/etcd-client/node-key.pem',"
                        "token=p['raft_bearer']),p['raft_key'],"
                        "pinned_cluster_id=p['raft_cluster']).snapshot();"
                        "print(json.dumps({'pending':len(v.document['pending']),"
                        "'completed':len(v.document.get('completed',{}))}))"
                    )
                state = docker(
                    "exec", writer if split else broker,
                    "python", "-c", verifier)
                counts = json.loads(state.stdout)
                if counts != {"pending": 3, "completed": 0}:
                    raise RuntimeError("INVALID_JOINED_RAFT_PENDING_COUNTS")
                if split:
                    # Even with a literal numeric IP, the graph broker
                    # cannot reach the raw etcd port or graph worker writer.
                    verify_isolation = (
                        "import socket,sys;"
                        "ip=sys.argv[1];port=int(sys.argv[2]);"
                        "s=socket.socket();s.settimeout(1.5);"
                        "status=s.connect_ex((ip,port));s.close();"
                        "sys.exit(0 if status != 0 else 4)"
                    )
                    raft_ip = (
                        external_writer["raft_ip"] if remote else
                        json.loads(
                            docker("inspect", "assistx-broker-quorum-r1-" +
                                   tag).stdout)[0]["NetworkSettings"][
                            "Networks"][raft_net]["IPAddress"])
                    denied = docker(
                        "exec", broker, "python", "-c", verify_isolation,
                        raft_ip,
                        str(external_writer["raft_port"] if remote else 2379),
                        timeout=8, check=False)
                    if denied.returncode != 0:
                        raise RuntimeError("BROKER_DIRECT_ETCD_BYPASS")
                    policy_ip = json.loads(
                        docker("inspect", writer).stdout)[0][
                        "NetworkSettings"]["Networks"][writer_net][
                        "IPAddress"]
                    denied = docker(
                        "exec", worker, "python", "-c", verify_isolation,
                        policy_ip, "8810", timeout=8, check=False)
                    if denied.returncode != 0:
                        raise RuntimeError("WORKER_DIRECT_POLICY_BYPASS")
                    for port in ("7687", "7474"):
                        denied = docker(
                            "exec", writer, "python", "-c",
                            verify_isolation, neo_ip, port,
                            timeout=8, check=False)
                        if denied.returncode != 0:
                            raise RuntimeError(
                                "WRITER_DIRECT_NEO4J_BYPASS")
                    # Read only an uncredentialed status; no token logged.
                    docker(
                        "cp", str(SOURCE / "trace_disposable_writer_unauth.py"),
                        broker + ":/tmp/writer-unauth.py")
                    unauth = docker(
                        "exec", broker, "python", "/tmp/writer-unauth.py",
                        timeout=9, check=False)
                    if unauth.returncode != 0:
                        raise RuntimeError(
                            "UNAUTHENTICATED_POLICY_WRITER_ACCEPTED")
                # Fault-inject only OUR locally created voter containers.
                # For remote 3-host voters, the parent harness separately
                # executes quorum loss; this callback must not stop them.
                if not remote:
                    for voter_i in (2, 3):
                        docker(
                            "stop", "-t", "2",
                            "assistx-broker-quorum-r" +
                            str(voter_i) + "-" + tag, timeout=12)
                # With one of three left, a fresh linearizable read/CAS
                # cannot be authorized; broker must deny before graph.
                if not remote:
                    docker(
                        "cp", str(SOURCE / "trace_disposable_joined_quorum_loss.py"),
                        worker + ":/tmp/minority.py")
                    minority = docker(
                        "exec", worker, "python", "/tmp/minority.py",
                        timeout=16, check=False)
                    if minority.returncode != 0:
                        raise RuntimeError("MINORITY_BROKER_DID_NOT_FAIL_CLOSED")
                    minority_result = json.loads(minority.stdout)
                    if not minority_result.get(
                            "disposable_two_voter_outage_denies_fresh_broker_read"):
                        raise RuntimeError("MINORITY_DENIAL_NOT_PROVEN")
                if remote and external_writer.get("fault_context") is not None:
                    fault = external_writer["fault_context"]
                    remote_shell = fault["shell"]
                    names = fault["container_names"]
                    stopped = []
                    try:
                        for host in ("xwing", "destroyer"):
                            name = names[host]
                            if not name.startswith("assistx-raft-"):
                                raise RuntimeError("UNSAFE_FAULT_TARGET")
                            remote_shell(
                                ["docker", "stop", "--time", "2", name],
                                host, timeout=20)
                            stopped.append((host, name))
                        docker(
                            "cp", str(SOURCE /
                                      "trace_disposable_joined_quorum_loss.py"),
                            worker + ":/tmp/minority.py")
                        minority = docker(
                            "exec", worker, "python", "/tmp/minority.py",
                            timeout=17, check=False)
                        if minority.returncode != 0:
                            raise RuntimeError(
                                "THREE_HOST_JOINED_MINORITY_NOT_DENIED")
                        minority_result = json.loads(minority.stdout)
                        if not minority_result.get(
                                "disposable_two_voter_outage_denies_fresh_broker_read"):
                            raise RuntimeError("THREE_HOST_MINOR_DENIAL_NOT_PROVEN")
                    finally:
                        # Restore ONLY the exact research voter names, even
                        # after a failed assertion. Parent harness owns their
                        # final teardown and all certificates.
                        for host, name in reversed(stopped):
                            remote_shell(
                                ["docker", "start", name],
                                host, timeout=25)
                    recovery_deadline = time.monotonic() + 45
                    while True:
                        checked = docker(
                            "exec", writer, "python", "-c", verifier,
                            timeout=10, check=False)
                        if checked.returncode == 0:
                            recovered = json.loads(checked.stdout)
                            if recovered == {"pending": 3, "completed": 0}:
                                break
                        if time.monotonic() >= recovery_deadline:
                            raise RuntimeError(
                                "THREE_HOST_JOINED_RAFT_RECOVERY_FAILED")
                        time.sleep(2)
            graph_restarted = False
            if (remote and os.getenv("ASSISTX_B06_GRAPH_RESTART_RESEARCH")
                    == "1"):
                # Research-only: restart exactly the fixture graph, NEVER
                # live Neo4j. Raft reservations MUST NOT clear on HTTP
                # success, missing graph connection, or graph restart.
                inspected = json.loads(docker("inspect", graph).stdout)[0]
                before_started = inspected["State"]["StartedAt"]
                docker("stop", "-t", "2", graph, timeout=18)
                during = docker(
                    "exec", writer, "python", "-c", verifier,
                    timeout=12, check=False)
                if during.returncode != 0 or json.loads(during.stdout) != {
                        "pending": 3, "completed": 0}:
                    raise RuntimeError("GRAPH_OUTAGE_FREED_RAFT_CAPACITY")
                docker("start", graph, timeout=18)
                deadline = time.monotonic() + 55
                while True:
                    health = query(graph, admin, "RETURN 1 AS ok",
                                   timeout=8)
                    if health.returncode == 0:
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError("DISPOSABLE_NEO4J_RESTART_FAILED")
                    time.sleep(2)
                after_started = json.loads(
                    docker("inspect", graph).stdout)[0]["State"]["StartedAt"]
                if after_started == before_started:
                    raise RuntimeError("GRAPH_RESTART_GENERATION_UNCHANGED")
                after = docker(
                    "exec", writer, "python", "-c", verifier, timeout=12)
                if json.loads(after.stdout) != {
                        "pending": 3, "completed": 0}:
                    raise RuntimeError("GRAPH_RESTART_PREMATURE_CLOSURE")
                graph_restarted = True
            return {
                **result,
                "b06_graph_restart_reservations_held": graph_restarted,
                "b06_independent_witness_integrated": False,
                "b06_explicit_broker_bolt_observed_active_and_absent":
                    b06_observed,
                "b06_separate_graph_principal_observer": b06_observed,
                "b06_server_boot_independently_attested": False,
                "b06_signed_closure_or_release_authorized": False,
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
                "broker_dual_network_membership": not joined,
                "broker_triple_network_membership": joined,
                "separate_policy_writer_container": split,
                "broker_no_raw_etcd_network_path": split,
                "worker_no_direct_policy_writer_path": split,
                "writer_no_direct_neo4j_path": split,
                "policy_writer_no_auth_denied": split,
                "independent_host_or_mtls_policy_auth": remote,
                "disposable_real_raft_CAS_before_Neo4j": joined,
                "disposable_real_raft_voters_same_host": joined and not remote,
                "distinct_physical_raft_hosts": remote,
                "external_three_host_mtls_rbac": remote,
                "disposable_two_voter_minority_denies_fresh_admission":
                    joined and (not remote or
                                external_writer.get("fault_context") is not None),
                "disposable_raft_tls_or_rbac": remote,
                "writer_to_raft_mtls_and_exact_key_RBAC": remote,
                "broker_to_writer_mtls": False,
                "distinct_writer_os_identity_verified": False,
                "independent_physical_witness": False,
                "production_neo4j_touched": False,
                "Raft_issuer_required_at_broker": joined,
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
