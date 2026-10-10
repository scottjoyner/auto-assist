"""Manual REAL Raft-quorum -> protected Neo4j Bolt -> detached closure witness.

Only called from the opt-in 3-host test AFTER quorum genesis. The Neo4j 5.26
instance is disposable, no published ports, persistent volumes or host binds.
A research host process (not an isolated worker) holds both Raft client and
Neo4j test access. THIS DOES NOT PROVE production network/Neo4j RBAC, cluster
generation fencing or independently immutable witness custody.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import multiprocessing as mp
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from neo4j import GraphDatabase

from trace_etcd_quorum_fence_research import (
    EtcdQuorumFence, EtcdTLS, FenceRefused, canon, takeover_request,
    witness_receipt,
)
from trace_etcd_graph_admission_research import QuorumPlanGrantAdapter
from trace_graph_entry_guarded import ProtectedGraphEntry, QueryPlan
from trace_graph_entry_metadata import gateway_metadata, observed_exact
from trace_graph_entry_research import AdmissionDenied
from trace_graph_entry_research_journal import SqliteResearchJournal

NEO4J_IMAGE = "neo4j:5.26-enterprise"


def _transactions(driver):
    with driver.session(database="system") as session:
        return [dict(row) for row in session.run(
            "SHOW TRANSACTIONS YIELD transactionId, database, metaData "
            "RETURN transactionId, database, metaData"
        )]


def _detached_witness(uri, endpoint, certs, key, cluster_id,
                      server, generation, channel):
    """Private witness signing key never leaves this child process."""
    signer = Ed25519PrivateKey.generate()
    public = signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    kv = EtcdTLS(endpoint, *certs)
    quorum = EtcdQuorumFence(kv, key, cluster_id)
    with GraphDatabase.driver(uri, auth=None, connection_timeout=3) as driver:
        driver.verify_connectivity()
        channel.send({"kind": "ready", "public": public.hex()})
        bindings = {}
        while True:
            request = channel.recv()
            if request["op"] == "stop":
                return
            if request["op"] == "observe":
                token, metadata = request["token"], request["metadata"]
                view = quorum.snapshot()
                pending = view.document["pending"].get(token)
                if not pending or pending["state"] != "RESERVED" or (
                    metadata.get("assistx_reservation_id") != token or
                    metadata.get("assistx_epoch") != view.document["genesis"] or
                    metadata.get("assistx_term") != pending["term"] or
                    pending["owner"] != view.document["owner"]):
                    channel.send({"result": "quorum-binding-denied"})
                    continue
                txid = None
                for _ in range(70):
                    txid = observed_exact(_transactions(driver), metadata)
                    if txid:
                        break
                    time.sleep(0.12)
                if not txid:
                    channel.send({"result": "server-metadata-not-observed"})
                    continue
                # Binding is a separate Raft CAS; it cannot be written by
                # a lone minority voter or behind a stale term.
                quorum.bind(token, pending["owner"], pending["term"],
                            server, generation, txid)
                bindings[token] = (txid, metadata)
                channel.send({"result": "bound", "txid": txid})
            elif request["op"] == "closure":
                token = request["token"]
                observed = bindings.get(token)
                if not observed:
                    channel.send({"result": "not-witnessed"})
                    continue
                txid, _ = observed
                absent = 0
                for _ in range(70):
                    rows = _transactions(driver)
                    absent = (absent + 1 if not any(
                        row["transactionId"] == txid for row in rows) else 0)
                    if absent >= 2:
                        break
                    time.sleep(0.12)
                if absent < 2:
                    channel.send({"result": "still-active"})
                    continue
                view = quorum.snapshot()
                pending = view.document["pending"].get(token)
                if not pending or pending["state"] != "STARTED" or (
                    pending["txid"] != txid or
                    pending["server"] != server or
                    pending["generation"] != generation):
                    channel.send({"result": "bound-state-changed"})
                    continue
                receipt = witness_receipt(view, token)
                signature = signer.sign(canon(receipt)).hex()
                del bindings[token]
                channel.send({"result": "signed-closure",
                              "receipt": receipt, "signature": signature})
            else:
                channel.send({"result": "unsupported"})


def run_real_quorum_graph(clients, cluster_id, base, run_id, ip_map,
                          client_port, certs, shell,
                          container_names, started, result):
    """Runs between the three-voter bootstrap and Raft partition scenario."""
    if os.getenv("ASSISTX_QUORUM_NEO4J_RESEARCH") != "1":
        raise RuntimeError("EXPLICIT_REAL_QUORUM_GRAPH_OPT_IN_REQUIRED")
    graph_name = "assistx-raft-neo526-" + run_id
    network = "assistx-raft-neo-net-" + run_id
    network_created = graph_created = False
    release_tx = threading.Event()
    graph_entered = threading.Event()
    witness_proc = None
    channel = None
    journal_home = tempfile.TemporaryDirectory(prefix="assistx-quorum-graph-")
    try:
        shell(["docker", "network", "create", "--internal",
               "--label", "assistx.trace.research=quorum-graph",
               network])
        network_created = True
        shell(["docker", "run", "-d", "--pull", "never",
               "--name", graph_name, "--network", network,
               "--memory", "2200m", "--cpus", "1.5", "--pids-limit", "256",
               "-e", "NEO4J_AUTH=none",
               "-e", "NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
               "-e", "NEO4J_server_memory_heap_initial__size=256m",
               "-e", "NEO4J_server_memory_heap_max__size=512m",
               NEO4J_IMAGE], timeout=28)
        graph_created = True
        import json
        obj = json.loads(shell(["docker", "inspect", graph_name]).stdout)[0]
        if (obj["HostConfig"]["NetworkMode"] != network or
                obj["Config"]["Image"] != NEO4J_IMAGE or
                obj["HostConfig"].get("PortBindings") or
                obj["HostConfig"].get("Binds") or
                any(m["Type"] == "bind" for m in obj.get("Mounts", []))):
            raise RuntimeError("UNSAFE_DISPOSABLE_NEO4J_FIXTURE")
        server = obj["Id"][:20]
        generation = run_id + "-first-boot"
        ip = obj["NetworkSettings"]["Networks"][network]["IPAddress"]
        uri = f"bolt://{ip}:7687"
        with GraphDatabase.driver(uri, auth=None,
                                  connection_timeout=3) as driver:
            deadline = time.monotonic() + 65
            while True:
                try:
                    driver.verify_connectivity()
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("DISPOSABLE_NEO4J_NOT_READY")
                    time.sleep(2)

            key = base + "/live-neo4j"
            coordinator = EtcdQuorumFence(clients["r1"], key, cluster_id)
            operator = Ed25519PrivateKey.generate()
            # The witness key is held ONLY by a fresh child process.
            ctx = mp.get_context("spawn")
            parent, child = ctx.Pipe()
            witness_proc = ctx.Process(
                target=_detached_witness,
                args=(uri, f"https://{ip_map['r1']}:{client_port}",
                      tuple(str(certs / "client" / filename) for filename in
                            ("ca.pem", "node.pem", "node-key.pem")),
                      key, cluster_id, server, generation, child)
            )
            witness_proc.start()
            channel = parent
            assert channel.poll(14), "DETACHED_WITNESS_NOT_READY"
            welcome = channel.recv()
            assert welcome["kind"] == "ready"
            witness_public = bytes.fromhex(welcome["public"])
            operator_public = operator.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            genesis = "graph-genesis-" + run_id
            coordinator.bootstrap(genesis, "gateway-old", operator_public,
                                  witness_public, capacity=1)
            # Must now pin cluster ID and owner in the actual bridge.
            raft = EtcdQuorumFence(clients["r2"], key, cluster_id)
            authority = QuorumPlanGrantAdapter(
                raft, "gateway-old", 1, genesis)
            journal = SqliteResearchJournal(
                Path(journal_home.name) / "gateway-ledger.sqlite")

            class Graph:
                calls = 0
                metadata = None
                token = None

                def execute(self, attempt, cypher, parameters):
                    self.calls += 1
                    self.metadata = gateway_metadata(attempt, "approved_read")
                    self.token = attempt.grant.token
                    with GraphDatabase.driver(uri, auth=None,
                                              connection_timeout=3) as owned:
                        with owned.session(database="neo4j") as session:
                            with session.begin_transaction(
                                    metadata=self.metadata, timeout=90) as tx:
                                answer = [row.data() for row in
                                          tx.run(cypher, **dict(parameters))]
                                graph_entered.set()
                                if not release_tx.wait(60):
                                    raise TimeoutError("RESEARCH_GRAPH_WAIT_EXPIRED")
                                return answer

            graph = Graph()
            entry = ProtectedGraphEntry(
                {"approved_read": QueryPlan(
                    "RETURN $value AS value", ("value",))},
                authority, journal, graph, genesis)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(entry.execute, "approved_read", {"value": 42})
                assert graph_entered.wait(12), "GATEWAY_REAL_GRAPH_ENTRY_MISSING"
                token = graph.token
                assert token is not None
                assert journal.verify_chain()[0] == 2
                view = coordinator.snapshot()
                assert token in view.document["pending"]
                channel.send({"op": "observe", "token": token,
                              "metadata": graph.metadata})
                assert channel.poll(15), "GRAPH_WITNESS_NO_RESPONSE"
                bound = channel.recv()
                assert bound["result"] == "bound", bound
                txid = bound["txid"]
                assert observed_exact(_transactions(driver), graph.metadata) == txid

                # We intentionally make the local Raft client a minority
                # while this REAL Neo4j transaction remains open.
                stopped = []
                try:
                    for host in ("xwing", "destroyer"):
                        shell(["docker", "stop", "--time", "2",
                               container_names[host]], host, timeout=20)
                        started.remove(host)
                        stopped.append(host)
                    try:
                        entry.execute("approved_read", {"value": 99})
                    except AdmissionDenied as exc:
                        assert str(exc) == "AUTHORITY_UNAVAILABLE", str(exc)
                    else:
                        raise AssertionError("NO_QUORUM_GRAPH_ADMISSION_SUCCEEDED")
                    assert graph.calls == 1, "MINORITY_STARTED_SECOND_BOLT_TRANSACTION"
                    assert any(row["transactionId"] == txid
                               for row in _transactions(driver)), (
                        "OLD_NEO4J_TRANSACTION_DID_NOT_SURVIVE_QUORUM_LOSS")
                finally:
                    for host in stopped:
                        shell(["docker", "start",
                               container_names[host]], host, timeout=22)
                        started.add(host)

                # Committed reservation must still be held after restart.
                deadline = time.monotonic() + 35
                while True:
                    try:
                        view = coordinator.snapshot()
                        if token in view.document["pending"]:
                            break
                    except FenceRefused:
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("RAFT_RECOVERY_PENDING_LOST")
                    time.sleep(1)
                try:
                    entry.execute("approved_read", {"value": 100})
                except AdmissionDenied as exc:
                    assert str(exc) == "AUTHORITY_UNAVAILABLE", str(exc)
                else:
                    raise AssertionError("PHYSICAL_CAPACITY_ALREADY_RELEASED")
                assert graph.calls == 1

                # Signed owner transition while old effects pending MUST fail.
                request = takeover_request(
                    coordinator.snapshot(), "gateway-successor")
                try:
                    coordinator.takeover(
                        "gateway-successor", request,
                        operator.sign(canon(request)))
                except FenceRefused as exc:
                    assert str(exc) == "UNCERTAIN_PHYSICAL_WORK_BLOCKS_TAKEOVER"
                else:
                    raise AssertionError("OLD_EFFECTS_NOT_ACCOUNTED_FOR")

                # The detached witness must refuse to sign while tx ACTIVE.
                channel.send({"op": "closure", "token": token})
                assert channel.poll(14), "EARLY_CLOSURE_WITNESS_NO_RESPONSE"
                assert channel.recv()["result"] == "still-active"
                release_tx.set()
                assert future.result(timeout=20) == [{"value": 42}]
                assert journal.verify_chain()[0] == 3
                assert token in coordinator.snapshot().document["pending"]

                channel.send({"op": "closure", "token": token})
                assert channel.poll(15), "PHYSICAL_CLOSURE_WITNESS_NO_RESPONSE"
                closure = channel.recv()
                assert closure["result"] == "signed-closure", closure
                coordinator.close_with_witness(
                    token, closure["receipt"],
                    bytes.fromhex(closure["signature"]))
                assert not coordinator.snapshot().document["pending"]
                try:
                    coordinator.close_with_witness(
                        token, closure["receipt"],
                        bytes.fromhex(closure["signature"]))
                except FenceRefused:
                    pass
                else:
                    raise AssertionError("CLOSURE_RECEIPT_REPLAY_ACCEPTED")
                # Only after the exact detached physical witness: next owner.
                current = coordinator.snapshot()
                takeover = takeover_request(current, "gateway-successor")
                assert coordinator.takeover(
                    "gateway-successor", takeover,
                    operator.sign(canon(takeover))) == 2
                try:
                    entry.execute("approved_read", {"value": 101})
                except AdmissionDenied as exc:
                    assert str(exc) == "AUTHORITY_UNAVAILABLE"
                else:
                    raise AssertionError("STALE_GATEWAY_REAL_BOLT_SUCCEEDED")
                assert graph.calls == 1

            # SECURITY NEGATIVE: mTLS authenticates the client, but because
            # etcd keyspace RBAC is NOT enabled here, the same raw KV client
            # can bypass the operator-signature policy entirely. Demonstrate
            # this only on a SEPARATE disposable consensus key.
            from trace_etcd_quorum_fence_research import b64
            import json
            raw_key = base + "/raw-kv-policy-bypass-negative"
            raw_control = EtcdQuorumFence(clients["r1"], raw_key, cluster_id)
            raw_control.bootstrap(
                "unprivileged-bypass-negative-" + run_id,
                "approved-owner", operator_public, witness_public, 1)
            raw_before = raw_control.snapshot()
            forged = json.loads(canon(raw_before.document))
            forged["term"] = 99
            forged["owner"] = "forged-raw-kv-writer"
            raw_response = clients["r1"].txn({
                "compare": [{
                    "key": b64(raw_key), "target": "MOD",
                    "result": "EQUAL",
                    "mod_revision": str(raw_before.mod_revision)
                }],
                "success": [{"request_put": {
                    "key": b64(raw_key), "value": b64(canon(forged))
                }}], "failure": [],
            })
            assert raw_response["succeeded"] is True, (
                "EXPECTED_RAW_KV_POLICY_BYPASS_NOT_REPRODUCED")
            raw_after = raw_control.snapshot()
            assert raw_after.document["term"] == 99
            assert raw_after.document["owner"] == "forged-raw-kv-writer"
            result["events"].append(
                "raw_authenticated_kv_writer_bypassed_signed_takeover_policy")

            result["events"].append(
                "real_neo4j_quorum_admission_before_bolt_committed")
            result["events"].append(
                "active_graph_tx_survived_two_voter_quorum_loss")
            result["events"].append(
                "minority_gateway_entry_denied_without_second_bolt")
            result["events"].append(
                "pending_graph_intent_blocked_signed_takeover")
            result["events"].append(
                "detached_witness_denied_active_then_signed_absence")
            result["events"].append(
                "signed_quorum_closure_then_term2_stale_gateway_denied")
            return {
                "physical_neo4j_query": True,
                "raft_consensus_reservation_before_real_bolt": True,
                "real_neo4j_transaction_survived_quorum_loss": True,
                "no_quorum_new_graph_entry_denied": True,
                "no_premature_capacity_release_after_driver_success": True,
                "detached_witness_verified_real_server_transaction": True,
                "witness_signed_after_two_absent_observations": True,
                "witness_receipt_replay_denied": True,
                "stale_gateway_after_term_change_denied": True,
                "server_enforced_neo4j_fencing": False,
                "witness_external_durable_custody": False,
                "etcd_scoped_kv_rbac_enforced": False,
                "authenticated_raw_kv_policy_bypass_reproduced": True,
                "graph_cluster_generation_witnessed": False,
                "production_authority": False,
            }
    finally:
        release_tx.set()
        if channel is not None and witness_proc is not None and witness_proc.is_alive():
            try:
                channel.send({"op": "stop"})
            except (BrokenPipeError, EOFError, OSError):
                pass
            witness_proc.join(4)
            if witness_proc.is_alive():
                witness_proc.kill()
                witness_proc.join(3)
        if graph_created:
            shell(["docker", "rm", "-f", "-v", graph_name],
                  timeout=25, check=False)
        if network_created:
            shell(["docker", "network", "rm", network],
                  timeout=20, check=False)
        journal_home.cleanup()
