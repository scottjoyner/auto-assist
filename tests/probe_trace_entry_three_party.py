"""Opt-in three-party PHYSICAL research: isolated caller -> gateway -> PG+Neo, verifier.

Only disposable PostgreSQL 17 / Neo4j 5.26 containers on private Docker
network, and a --network=none worker with an explicit read-only UNIX IPC mount.
The host harness is PRIVILEGED and CAN reach Neo4j directly. This is not a
production trust boundary, monotonic fencing, or quorum-failover acceptance.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

import psycopg
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from neo4j import GraphDatabase

from probe_trace_pg_privileged_roles import _dsn, bootstrap_once, WORKER, VERIFIER
from probe_trace_transport_blackhole_526 import _Proxy
from trace_graph_entry_guarded import ProtectedGraphEntry, QueryPlan
from trace_graph_entry_metadata import gateway_metadata, observed_exact
from trace_graph_entry_research import AdmissionDenied, Grant
from trace_graph_entry_research_journal import SqliteResearchJournal


def docker(*args, timeout=25, check=True):
    try:
        return subprocess.run(["docker", *args], capture_output=True,
                              text=True, check=check, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Never leak temporary PostgreSQL passwords in command error strings.
        raise RuntimeError("DISPOSABLE_DOCKER_COMMAND_FAILED") from None


def _snapshot(driver):
    with driver.session(database="system") as s:
        return [dict(x) for x in s.run(
            "SHOW TRANSACTIONS YIELD transactionId, database, metaData "
            "RETURN transactionId, database, metaData"
        )]


def _pg_admit(dsn, epoch, token, ref):
    with psycopg.connect(dsn, connect_timeout=3) as db:
        result = db.execute(
            "SELECT * FROM assistx_trace_fence_research.admit(%s,%s,%s)",
            (epoch, token, ref)
        ).fetchone()
        db.commit()
        return result


def _pg_capacity(dsn, epoch):
    with psycopg.connect(dsn, connect_timeout=3) as db:
        return db.execute(
            "SELECT assistx_trace_fence_research.inspect(%s)", (epoch,)
        ).fetchone()[0]


def _gateway(socket_path, pg_dsn, neo_uri, epoch, event_pipe, journal_path):
    """Receives ONLY a plan identifier and parameters, no worker authority."""
    class Authority:
        def admit(self, operation_id, plan_id):
            token = secrets.token_hex(16)
            accepted, _, reason = _pg_admit(pg_dsn, epoch, token, operation_id)
            if not accepted:
                return None
            # Term 1 is NOT PG-signed or monotonic. Fixture placeholder.
            return Grant(operation_id, token, 1, epoch)

    class Graph:
        def execute(self, attempt, cypher, params):
            metadata = gateway_metadata(attempt, "expensive_read")
            event_pipe.send({
                "kind": "graph_starting", "metadata": metadata,
                "token": attempt.grant.token, "ref": attempt.operation_id,
            })
            with GraphDatabase.driver(
                neo_uri, auth=None, connection_timeout=3,
                connection_acquisition_timeout=4
            ) as driver:
                with driver.session(database="neo4j") as session:
                    with session.begin_transaction(metadata=metadata, timeout=35) as tx:
                        return [row.data() for row in tx.run(cypher, **dict(params))]
    plan = QueryPlan(
        "UNWIND range(1,110000) AS n UNWIND range(1,110000) AS m "
        "WITH n,m WHERE (n*m)%97=7 RETURN count(*) AS count",
        ()
    )
    entry = ProtectedGraphEntry(
        {"expensive_read": plan}, Authority(),
        SqliteResearchJournal(journal_path), Graph(), epoch
    )
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(socket_path)
    os.chmod(socket_path, 0o666)  # fixture-only socket; not production ACL
    sock.listen(2)
    event_pipe.send({"kind": "gateway_ready"})
    try:
        for _ in range(2):
            conn, _ = sock.accept()
            with conn:
                message = b""
                while b"\n" not in message and len(message) < 4096:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    message += chunk
                try:
                    request = json.loads(message.split(b"\n", 1)[0])
                    result = entry.execute(
                        request["plan_id"], request["parameters"]
                    )
                    reply = {"result": result}
                except AdmissionDenied as exc:
                    reply = {"denied": str(exc)}
                except (ValueError, KeyError, TypeError):
                    reply = {"denied": "INVALID_WORKER_MESSAGE"}
                conn.sendall((json.dumps(reply) + "\n").encode())
    finally:
        sock.close()


def _append_custody(path, receipt, signature):
    """Append and fsync BEFORE verifier SQL release; research-only local file."""
    data = (json.dumps({"receipt": receipt, "signature": signature},
                       sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("CUSTODY_WRITE_FAILED")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _witness(pg_dsn, neo_uri, epoch, conn, custody_path):
    """Only this process receives verifier credential and ephemeral signer."""
    signer = Ed25519PrivateKey.generate()
    pub = signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    bindings = {}
    force_custody_failure = False
    with GraphDatabase.driver(neo_uri, auth=None, connection_timeout=3) as driver:
        conn.send({"kind": "witness_ready", "public": pub})
        while True:
            cmd = conn.recv()
            if cmd["op"] == "stop":
                return
            if cmd["op"] == "fault_custody":
                force_custody_failure = cmd["enabled"] is True
                conn.send({"result": "fault-mode-updated"})
                continue
            if cmd["op"] == "observe":
                metadata, token, ref = cmd["metadata"], cmd["token"], cmd["ref"]
                # Verifier independently checks *actual PostgreSQL row*.
                with psycopg.connect(pg_dsn, connect_timeout=3) as db:
                    bound = db.execute(
                        "SELECT assistx_trace_fence_research.verify_binding(%s,%s,%s)",
                        (epoch, token, ref)
                    ).fetchone()[0]
                if not bound or metadata.get("assistx_operation_id") != ref:
                    conn.send({"result": "pg-binding-denied"})
                    continue
                txid = None
                for _ in range(70):
                    txid = observed_exact(_snapshot(driver), metadata)
                    if txid:
                        break
                    time.sleep(0.12)
                if txid:
                    bindings[ref] = (txid, token, metadata)
                conn.send({"result": "observed" if txid else "not-observed",
                           "txid": txid})
                continue
            if cmd["op"] != "release":
                conn.send({"result": "unsupported"})
                continue
            ref, token = cmd["ref"], cmd["token"]
            binding = bindings.get(ref)
            if not binding or binding[1] != token:
                conn.send({"result": "wrong-token-or-unobserved"})
                continue
            txid = binding[0]
            absent = 0
            try:
                for _ in range(65):
                    rows = _snapshot(driver)
                    absent = (absent + 1) if not any(
                        row["transactionId"] == txid for row in rows
                    ) else 0
                    if absent >= 2:
                        break
                    time.sleep(0.13)
            except Exception:
                conn.send({"result": "graph-unavailable"})
                continue
            if absent < 2:
                conn.send({"result": "still-active"})
                continue
            # The privilege-bearing witness is the ONLY party that can
            # release. Do not let audit IO failure free physical capacity.
            with psycopg.connect(pg_dsn, connect_timeout=3) as db:
                current = db.execute(
                    "SELECT assistx_trace_fence_research.verify_binding(%s,%s,%s)",
                    (epoch, token, ref)
                ).fetchone()[0]
            if not current:
                conn.send({"result": "no-exact-reservation"})
                continue
            receipt = {
                "schema": "assistx-three-party-closure-research-v2",
                "epoch": epoch, "ref": ref, "txid": txid,
                "operation_id": binding[2]["assistx_operation_id"],
                "attempt_id": binding[2]["assistx_attempt_id"],
                "token_digest": hashlib.sha256(
                    (epoch + ":" + token).encode()).hexdigest(),
                "observation": "two-successive-absent-snapshots",
                "role": "separate-postgres-verifier-process",
            }
            canonical = json.dumps(
                receipt, sort_keys=True, separators=(",", ":")
            ).encode()
            signature = signer.sign(canonical).hex()
            try:
                _append_custody(
                    "/dev/full" if force_custody_failure else custody_path,
                    receipt, signature
                )
            except OSError:
                conn.send({"result": "custody-unavailable"})
                continue
            with psycopg.connect(pg_dsn, connect_timeout=3) as db:
                released = db.execute(
                    "SELECT assistx_trace_fence_research.release_exact(%s,%s,%s)",
                    (epoch, token, ref)
                ).fetchone()[0]
                db.commit()
            if not released:
                conn.send({"result": "no-exact-reservation"})
                continue
            del bindings[ref]
            conn.send({"result": "released", "receipt": receipt,
                       "signature": signature})


def _fixture_ip(container, network, expected_image):
    obj = json.loads(docker("inspect", container).stdout)[0]
    host = obj["HostConfig"]
    if (obj["Name"] != "/" + container
            or obj["Config"]["Image"] != expected_image
            or host["NetworkMode"] != network
            or set(obj["NetworkSettings"]["Networks"]) != {network}
            or host.get("PortBindings") or host.get("Binds")
            or any(m["Type"] == "bind" for m in obj.get("Mounts", []))):
        raise RuntimeError("UNSAFE_DISPOSABLE_FIXTURE")
    return obj["NetworkSettings"]["Networks"][network]["IPAddress"]


def run():
    if os.getenv("ASSISTX_THREE_PARTY_PHYSICAL_RESEARCH") != "1":
        raise RuntimeError("EXPLICIT_THREE_PARTY_OPT_IN_REQUIRED")
    ident = secrets.token_hex(4)
    network = "assistx-3party-back-" + ident
    pg = "assistx-3party-pg17-" + ident
    neo = "assistx-3party-neo526-" + ident
    worker = "assistx-3party-worker-" + ident
    admin_pw = secrets.token_hex(24)
    worker_pw = secrets.token_hex(24)
    verifier_pw = secrets.token_hex(24)
    epoch = str(uuid4())
    containers, network_created = [], False
    gateway = witness = proxy = server = None
    ctx = mp.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="assistx-three-party-") as home:
        os.chmod(home, 0o755)
        ipc_home = Path(home) / "worker-ipc"
        ipc_home.mkdir(mode=0o755)
        socket_path = str(ipc_home / "gateway.sock")
        journal_path = str(Path(home) / "gateway-ledger.sqlite")
        custody_path = str(Path(home) / "witness-closure.jsonl")
        try:
            docker("network", "create", "--internal", "--driver", "bridge",
                   "--label", "assistx.trace.research=three-party", network)
            network_created = True
            docker("run", "-d", "--name", pg, "--pull", "never",
                   "--network", network, "--cpus", "1", "--memory", "512m",
                   "-e", "POSTGRES_PASSWORD=" + admin_pw,
                   "-e", "POSTGRES_HOST_AUTH_METHOD=scram-sha-256",
                   "postgres:17-alpine")
            containers.append(pg)
            docker("run", "-d", "--name", neo, "--pull", "never",
                   "--network", network, "--cpus", "1.5", "--memory", "2200m",
                   "--pids-limit", "256", "-e", "NEO4J_AUTH=none",
                   "-e", "NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
                   "-e", "NEO4J_server_memory_heap_initial__size=256m",
                   "-e", "NEO4J_server_memory_heap_max__size=512m",
                   "neo4j:5.26-enterprise")
            containers.append(neo)
            pg_ip = _fixture_ip(pg, network, "postgres:17-alpine")
            neo_ip = _fixture_ip(neo, network, "neo4j:5.26-enterprise")
            admin_dsn = _dsn("postgres", admin_pw, pg_ip)
            pg_worker_dsn = _dsn(WORKER, worker_pw, pg_ip)
            pg_verifier_dsn = _dsn(VERIFIER, verifier_pw, pg_ip)
            for _ in range(45):
                try:
                    with psycopg.connect(admin_dsn, connect_timeout=2):
                        break
                except psycopg.Error:
                    time.sleep(1)
            else:
                raise RuntimeError("POSTGRES_FIXTURE_NOT_READY")
            bootstrap_once(admin_dsn, worker_pw, verifier_pw, epoch)
            with psycopg.connect(admin_dsn) as db:
                db.execute(
                    "UPDATE assistx_trace_fence_research.authority SET capacity=1"
                )
                db.execute("""
                    CREATE FUNCTION assistx_trace_fence_research.verify_binding(
                        e TEXT,t TEXT,r TEXT
                    ) RETURNS BOOLEAN LANGUAGE sql SECURITY DEFINER
                    SET search_path=pg_catalog,pg_temp AS $$
                      SELECT EXISTS(
                        SELECT 1 FROM assistx_trace_fence_research.reservations
                        WHERE epoch=e AND token=t AND query_ref=r
                      )
                    $$;
                    REVOKE ALL ON FUNCTION
                      assistx_trace_fence_research.verify_binding(text,text,text)
                      FROM PUBLIC;
                    GRANT EXECUTE ON FUNCTION
                      assistx_trace_fence_research.verify_binding(text,text,text)
                      TO assistx_trace_research_verifier;
                """)
            real_uri = "bolt://" + neo_ip + ":7687"
            with GraphDatabase.driver(real_uri, auth=None,
                                      connection_timeout=3) as driver:
                for _ in range(45):
                    try:
                        driver.verify_connectivity()
                        break
                    except Exception:
                        time.sleep(1)
                else:
                    raise RuntimeError("NEO4J_FIXTURE_NOT_READY")
            # Socket relay keeps Neo4j's upstream Bolt alive after gateway dies.
            proxy = _Proxy(("127.0.0.1", 0), neo_ip)
            server = threading.Thread(target=proxy.serve_forever, daemon=True)
            server.start()
            relay_uri = "bolt://127.0.0.1:" + str(proxy.server_address[1])
            gparent, gchild = ctx.Pipe()
            wparent, wchild = ctx.Pipe()
            witness = ctx.Process(
                target=_witness, args=(pg_verifier_dsn, real_uri, epoch, wchild, custody_path)
            )
            gateway = ctx.Process(
                target=_gateway,
                args=(socket_path, pg_worker_dsn, relay_uri, epoch,
                      gchild, journal_path)
            )
            witness.start()
            assert wparent.poll(14), "WITNESS_NOT_READY"
            public = wparent.recv()["public"]
            gateway.start()
            assert gparent.poll(14), "GATEWAY_NOT_READY"
            assert gparent.recv()["kind"] == "gateway_ready"

            # The only worker-to-gateway interface is a readonly-mounted
            # UNIX socket. Worker has --network=none and NO PG/Neo credentials.
            worker_code = (
                "import socket,json\n"
                "s=socket.socket();s.settimeout(1)\n"
                "try:\n"
                " s.connect((" + repr(neo_ip) + ",7687)); raise SystemExit('BYPASS')\n"
                "except OSError: pass\n"
                "for name in ['RETURN 42','expensive_read']:\n"
                " c=socket.socket(socket.AF_UNIX);c.connect('/ipc/gateway.sock')\n"
                " c.sendall((json.dumps({'plan_id':name,'parameters':{}})+'\\n').encode())\n"
                " print(c.recv(4096).decode(),flush=True);c.close()\n"
            )
            docker("run", "-d", "--name", worker, "--pull", "never",
                   "--network", "none", "--read-only",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--user", "65534:65534", "--memory", "128m",
                   "--pids-limit", "32",
                   "--mount", "type=bind,source=" + str(ipc_home) +
                   ",target=/ipc,readonly",
                   "python:3.12-slim", "python", "-c", worker_code)
            containers.append(worker)
            inspected = json.loads(docker("inspect", worker).stdout)[0]
            mounts = inspected.get("Mounts", [])
            if (inspected["HostConfig"]["NetworkMode"] != "none"
                    or not inspected["HostConfig"]["ReadonlyRootfs"]
                    or len(mounts) != 1
                    or mounts[0]["Source"] != str(ipc_home)
                    or mounts[0]["RW"] is not False
                    or inspected["HostConfig"].get("PortBindings")
                    or any("POSTGRES" in entry or "NEO4J" in entry
                           for entry in inspected["Config"].get("Env", []))):
                raise RuntimeError("WORKER_ISOLATION_BROKEN")
            # First worker request is an arbitrary Cypher string. Reject it
            # without obtaining a reservation or touching Neo4j.
            initial_logs = ""
            for _ in range(25):
                initial_logs = docker("logs", worker, check=False).stdout
                if "UNREGISTERED_QUERY_PLAN" in initial_logs:
                    break
                time.sleep(0.12)
            assert "UNREGISTERED_QUERY_PLAN" in initial_logs, "WORKER_DENIAL_NOT_OBSERVED"
            assert gparent.poll(17), "GATEWAY_DID_NOT_EXECUTE_GRAPH"
            attempt = gparent.recv()
            assert attempt["kind"] == "graph_starting", attempt
            metadata, token, ref = (
                attempt["metadata"], attempt["token"], attempt["ref"]
            )
            # The worker cannot forge any of these via the plan-only interface.
            assert metadata["assistx_operation_id"] == ref
            assert _pg_capacity(pg_worker_dsn, epoch) == 1
            assert SqliteResearchJournal(journal_path).verify_chain()[0] == 2
            # Worker-role SQL cannot invoke verifier-only release at all.
            with psycopg.connect(pg_worker_dsn, connect_timeout=3) as db:
                try:
                    db.execute(
                        "SELECT assistx_trace_fence_research.release_exact(%s,%s,%s)",
                        (epoch, token, ref)
                    ).fetchone()
                except (psycopg.errors.InsufficientPrivilege,
                        psycopg.errors.UndefinedFunction):
                    pass
                else:
                    raise AssertionError("WORKER_SQL_RELEASE_BYPASS")
            # Even correct Neo4j metadata is insufficient if the proposed
            # PostgreSQL reservation binding does not exist.
            wparent.send({"op": "observe", **dict(attempt, ref="forged-ref")})
            assert wparent.poll(6)
            assert wparent.recv()["result"] == "pg-binding-denied"
            wparent.send({"op": "observe", **attempt})
            assert wparent.poll(16), "OBSERVER_NO_BINDING"
            observation = wparent.recv()
            assert observation["result"] == "observed", observation
            txid = observation["txid"]
            assert re.fullmatch(r"neo4j-transaction-[0-9]+", txid)
            wparent.send({"op": "release", "token": "0" * 32, "ref": ref})
            assert wparent.poll(4)
            assert wparent.recv()["result"] == "wrong-token-or-unobserved"
            wparent.send({"op": "release", "token": token, "ref": ref})
            assert wparent.poll(12)
            assert wparent.recv()["result"] == "still-active"

            # On a paused, disposable PG primary no successor admission is
            # authorized. This is NOT a quorum/takeover experiment.
            docker("pause", pg, timeout=12)
            try:
                try:
                    unavailable = _pg_admit(
                        pg_worker_dsn, epoch, secrets.token_hex(16),
                        "pg-outage-successor"
                    )
                except psycopg.Error:
                    pass  # Fail closed: no PostgreSQL authority response.
                else:
                    raise AssertionError(
                        "PG_OUTAGE_GRANTED_OR_RETURNED: " + str(unavailable)
                    )
            finally:
                docker("unpause", pg, timeout=12)
            assert _pg_capacity(pg_worker_dsn, epoch) == 1

            # Reproduce uncertain physical state: upstream Bolt socket survives.
            proxy.blackout.set()
            time.sleep(.2)
            docker("rm", "-f", "-v", worker, check=False)
            containers.remove(worker)
            gateway.kill()
            gateway.join(4)
            assert gateway.exitcode == -9
            with GraphDatabase.driver(real_uri, auth=None) as observer_driver:
                assert any(row["transactionId"] == txid
                           for row in _snapshot(observer_driver))
            assert _pg_capacity(pg_worker_dsn, epoch) == 1
            denied = _pg_admit(pg_worker_dsn, epoch, secrets.token_hex(16),
                               "successor-before-closure")
            assert denied[0] is False and denied[2] == "full", denied
            wparent.send({"op": "release", "token": token, "ref": ref})
            assert wparent.poll(13)
            assert wparent.recv()["result"] == "still-active"

            # Closing the stalled proxy is NOT the same as a client timeout.
            proxy.quit.set()
            proxy.blackout.clear()
            # /dev/full reliably simulates a failed append/fsync without
            # touching any research or production data files.
            wparent.send({"op": "fault_custody", "enabled": True})
            assert wparent.poll(4)
            assert wparent.recv()["result"] == "fault-mode-updated"
            wparent.send({"op": "release", "token": token, "ref": ref})
            assert wparent.poll(17), "CUSTODY_FAILURE_TEST_STALLED"
            assert wparent.recv()["result"] == "custody-unavailable"
            assert _pg_capacity(pg_worker_dsn, epoch) == 1
            wparent.send({"op": "fault_custody", "enabled": False})
            assert wparent.poll(4)
            assert wparent.recv()["result"] == "fault-mode-updated"
            wparent.send({"op": "release", "token": token, "ref": ref})
            assert wparent.poll(17), "NO_CLOSURE_RECEIPT"
            proof = wparent.recv()
            assert proof["result"] == "released", proof
            signed_bytes = json.dumps(
                proof["receipt"], sort_keys=True, separators=(",", ":")
            ).encode()
            verifier = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public))
            verifier.verify(bytes.fromhex(proof["signature"]), signed_bytes)
            custody = [json.loads(line) for line in
                       Path(custody_path).read_text().splitlines()]
            assert len(custody) == 1
            assert custody[0] == {
                "receipt": proof["receipt"], "signature": proof["signature"]
            }
            assert len(proof["receipt"]["token_digest"]) == 64
            assert token not in Path(custody_path).read_text()
            from cryptography.exceptions import InvalidSignature
            bad_receipt = dict(proof["receipt"], ref="tampered")
            try:
                verifier.verify(
                    bytes.fromhex(proof["signature"]),
                    json.dumps(bad_receipt, sort_keys=True,
                               separators=(",", ":")).encode()
                )
            except InvalidSignature:
                pass
            else:
                raise AssertionError("TAMPERED_RECEIPT_SIGNATURE_ACCEPTED")
            assert _pg_capacity(pg_worker_dsn, epoch) == 0
            successor = _pg_admit(pg_worker_dsn, epoch, secrets.token_hex(16),
                                  "successor-after-proof")
            assert successor[0] is True
            assert SqliteResearchJournal(journal_path).verify_chain()[0] == 2
            wparent.send({"op": "release", "token": token, "ref": ref})
            assert wparent.poll(4)
            assert wparent.recv()["result"] == "wrong-token-or-unobserved"
            wparent.send({"op": "stop"})
            witness.join(5)
            assert witness.exitcode == 0
            return {
                "schema": "assistx-three-party-physical-v1",
                "worker_network": "none", "worker_direct_bolt_denied": True,
                "worker_only_unix_plan_request": True,
                "worker_mount_only_ipc_socket": True,
                "worker_rootfs_readonly_no_capabilities": True,
                "arbitrary_worker_cypher_denied": True,
                "worker_pg_release_denied": True,
                "forged_pg_binding_denied": True,
                "tampered_signature_denied": True,
                "pg_restricted_grant_committed": True,
                "neo4j_server_metadata_bound": True,
                "verifier_pg_exact_binding_checked": True,
                "wrong_token_denied": True,
                "active_query_release_denied": True,
                "worker_and_gateway_killed": True,
                "neo4j_transaction_survived_kill": True,
                "postgres_slot_retained_after_crash": True,
                "successor_denied_until_closure": True,
                "independent_verifier_release": True,
                "detached_signature_verified": True,
                "fsynced_witness_receipt_precedes_sql_release": True,
                "audit_write_failure_retains_pg_capacity": True,
                "pg_primary_pause_denied_successor": True,
                "successor_admitted_after_proof": True,
                "gateway_audit_events_after_crash": 2,
                "production_authority": False,
                "pg_quorum_fencing_term": False,
                "server_issued_neo4j_permit": False,
                "signed_append_only_custody": False,
                "distributed_failover_proven": False,
            }
        finally:
            if gateway and gateway.is_alive():
                gateway.kill()
                gateway.join(4)
            if witness and witness.is_alive():
                witness.kill()
                witness.join(4)
            if proxy:
                proxy.quit.set()
                proxy.blackout.clear()
                proxy.shutdown()
                proxy.server_close()
            if server:
                server.join(4)
            for container in reversed(containers):
                docker("rm", "-f", "-v", container, check=False)
            if network_created:
                docker("network", "rm", network, check=False)


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
