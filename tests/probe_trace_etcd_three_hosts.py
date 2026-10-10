"""Opt-in genuine 3-HOST Raft quorum research, never production or failover.

Voters: x1-370, xwing, destroyer (separate physical machines, one tailnet).
Uses TEMP mTLS etcd v3.6.14 containers bound ONLY to Tailscale IPv4s,
no published ports, no host service edits, no external/VPN ACL changes.
A unique Raft cluster and unique application namespace are created each run.
No actual Neo4j transaction, SQL, or production trace worker is invoked.
Requires prior explicit image pull, SSH keys and Docker on all three hosts.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import ssl
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ipaddress
import json
import os
import secrets
import shlex
import shutil
import socket
import subprocess
import time

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from trace_etcd_quorum_fence_research import (
    EtcdTLS, EtcdQuorumFence, FenceRefused, b64, canon, takeover_request
)

IMAGE = "quay.io/coreos/etcd:v3.6.14"
HOSTS = [("r1", "x1-370"), ("r2", "xwing"), ("r3", "destroyer")]
HOME = Path("/home/scott/git")


def shell(args: list[str], host="x1-370", timeout=35, check=True, input=None):
    if host == "x1-370":
        command = args
    else:
        command = ["ssh", "-o", "BatchMode=yes", "-o",
                   "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=5",
                   host, shlex.join(args)]
    try:
        return subprocess.run(command, input=input, capture_output=True,
                              check=check, text=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise RuntimeError("SCOPED_RESEARCH_COMMAND_FAILED:" + host) from None


def pem_key(key):
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def pem_cert(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def provision_certificates(folder: Path, ip_map: dict) -> None:
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(
        NameOID.COMMON_NAME, "AssistX disposable Raft research CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=2))
          .not_valid_after(now + timedelta(hours=3))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0),
                         critical=True)
          .sign(ca_key, hashes.SHA256()))
    (folder / "ca.pem").write_bytes(pem_cert(ca))
    (folder / "ca.key").write_bytes(pem_key(ca_key))
    os.chmod(folder / "ca.key", 0o600)

    def leaf(name, ip=None):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        builder = (x509.CertificateBuilder().subject_name(subject)
                   .issuer_name(ca_name).public_key(private.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=2))
                   .not_valid_after(now + timedelta(hours=2))
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                                  critical=True)
                   .add_extension(x509.ExtendedKeyUsage([
                       ExtendedKeyUsageOID.SERVER_AUTH,
                       ExtendedKeyUsageOID.CLIENT_AUTH,
                   ]), critical=False))
        if ip:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([
                    x509.IPAddress(ipaddress.ip_address(ip))]), critical=False)
        certificate = builder.sign(ca_key, hashes.SHA256())
        out = folder / name
        out.mkdir(mode=0o700)
        (out / "ca.pem").write_bytes(pem_cert(ca))
        (out / "node.pem").write_bytes(pem_cert(certificate))
        (out / "node-key.pem").write_bytes(pem_key(private))
        os.chmod(out / "node-key.pem", 0o600)

    for node, _ in HOSTS:
        leaf(node, ip_map[node])
    # This is a separate mTLS client identity from the three voting nodes.
    leaf("client")


def run():
    if os.environ.get("ASSISTX_THREE_HOST_RAFT_RESEARCH") != "1":
        raise RuntimeError("EXPLICIT_THREE_HOST_RAFT_OPT_IN_REQUIRED")
    run_id = secrets.token_hex(5)
    client_port = 30000 + secrets.randbelow(7000)
    peer_port = client_port + 1
    prefix = "assistx-raft-" + run_id
    staging = HOME / ("." + prefix)
    if staging.exists():
        raise RuntimeError("RESEARCH_STAGING_ALREADY_EXISTS")
    staging.mkdir(mode=0o700)
    result = {
        "schema": "assistx-three-host-etcd-raft-quorum-v1",
        "hosts": [h for _, h in HOSTS],
        "image": IMAGE,
        "fixture_only": True, "production_authority": False,
        "real_neo4j_stale_effect_rejected": False,
        "graph_witness_integrated": False,
        "signed_external_custody_integrated": False,
        "multi_site_failure_domains": False,
        "operator_auto_failover": False,
        "app_term_independently_enforced_in_graph": False,
        "events": [],
    }
    container_names = {}
    stage_hosts = set()
    started = set()
    try:
        ip_map = {}
        for node, host in HOSTS:
            ip = shell(["tailscale", "ip", "-4"], host).stdout.strip().splitlines()[0]
            ipaddress.IPv4Address(ip)
            ip_map[node] = ip
            # No pre-existing service on chosen IP and test-only ports.
            bind_check = (
                "import socket,sys; a=sys.argv[1]; ps=list(map(int,sys.argv[2:])); "
                "ss=[]; "
                "[(lambda s,p:(s.bind((a,p)),ss.append(s)))(socket.socket(),p) "
                "for p in ps]; [s.close() for s in ss]"
            )
            shell(["python3", "-c", bind_check, ip, str(client_port),
                   str(peer_port)], host)
            result["events"].append("host_preflight:" + host)
        certs = staging / "certs"
        certs.mkdir(mode=0o700)
        provision_certificates(certs, ip_map)
        # Each remote receives only its node cert and CA, never CA private
        # key or client credential. Private material remains ephemeral.
        for node, host in HOSTS:
            remote = str(staging / "certs")
            if host != "x1-370":
                shell(["mkdir", "-p", remote], host)
                stage_hosts.add(host)
                for filename in ("ca.pem", "node.pem", "node-key.pem"):
                    source = certs / node / filename
                    command = ["scp", "-q", "-o", "BatchMode=yes",
                               "-o", "StrictHostKeyChecking=yes",
                               str(source), host + ":" + remote + "/" + filename]
                    subprocess.run(command, capture_output=True, check=True,
                                   timeout=18)
                shell(["chmod", "700", remote], host)
                shell(["chmod", "600", remote + "/node-key.pem"], host)
            container_names[host] = prefix + "-" + node
        cluster = ",".join(
            f"{node}=https://{ip_map[node]}:{peer_port}"
            for node, _ in HOSTS)
        for node, host in HOSTS:
            name = container_names[host]
            node_certs = str(certs / node if host == "x1-370"
                             else staging / "certs")
            data_dir = str(staging / node / "data")
            shell(["mkdir", "-p", data_dir], host)
            shell(["chmod", "700", data_dir], host)
            # All three real hosts' scott accounts have UID/GID 1000.
            # Run etcd with that identity, not root: it can read only its
            # own mode 0700/0600 temporary files without Linux capabilities.
            args = [
                "docker", "run", "-d", "--name", name, "--pull", "never",
                "--network", "host", "--user", "1000:1000",
                "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--memory", "384m", "--cpus", "0.8", "--pids-limit", "128",
                "--mount", f"type=bind,src={data_dir},dst=/etcd-data",
                "--mount", f"type=bind,src={node_certs},dst=/certs,readonly",
                "--label", "assistx.trace.research=quorum",
                IMAGE, "/usr/local/bin/etcd",
                "--name", node,
                "--data-dir", "/etcd-data",
                "--listen-client-urls",
                f"https://{ip_map[node]}:{client_port}",
                "--advertise-client-urls",
                f"https://{ip_map[node]}:{client_port}",
                "--listen-peer-urls", f"https://{ip_map[node]}:{peer_port}",
                "--initial-advertise-peer-urls",
                f"https://{ip_map[node]}:{peer_port}",
                "--initial-cluster", cluster,
                "--initial-cluster-token", prefix,
                "--initial-cluster-state", "new",
                "--client-cert-auth",
                "--trusted-ca-file", "/certs/ca.pem",
                "--cert-file", "/certs/node.pem",
                "--key-file", "/certs/node-key.pem",
                "--peer-client-cert-auth",
                "--peer-trusted-ca-file", "/certs/ca.pem",
                "--peer-cert-file", "/certs/node.pem",
                "--peer-key-file", "/certs/node-key.pem",
                "--logger", "zap", "--log-level", "warn",
            ]
            shell(args, host, timeout=30)
            started.add(host)
            result["events"].append("vote_container_started:" + host)

        def endpoint(node):
            return EtcdTLS(
                f"https://{ip_map[node]}:{client_port}",
                str(certs / "client" / "ca.pem"),
                str(certs / "client" / "node.pem"),
                str(certs / "client" / "node-key.pem"))
        clients = {node: endpoint(node) for node, _ in HOSTS}
        base = "/assistx/research/fencing/" + run_id
        gateway = EtcdQuorumFence(clients["r1"], base + "/active")
        witness = ed25519.Ed25519PrivateKey.generate()
        operator = ed25519.Ed25519PrivateKey.generate()
        pubkey = lambda key: key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        deadline = time.monotonic() + 65
        while True:
            try:
                cluster_id = gateway.bootstrap(
                    "genesis-" + run_id, "gateway-old", pubkey(operator),
                    pubkey(witness), 1)
                break
            except FenceRefused as exc:
                if time.monotonic() >= deadline:
                    diagnostics = {}
                    for _, host in HOSTS:
                        name = container_names[host]
                        try:
                            state = shell(
                                ["docker", "inspect", "-f",
                                 "{{.State.Status}} {{.State.ExitCode}}",
                                 name], host, check=False).stdout.strip()
                            logs = shell(
                                ["docker", "logs", "--tail", "35", name],
                                host, check=False).stderr[-5000:]
                            diagnostics[host] = {"state": state, "logs": logs}
                        except Exception:
                            diagnostics[host] = {"read_error": True}
                    print(json.dumps({"startup_diagnostics": diagnostics},
                                     sort_keys=True), flush=True)
                    raise RuntimeError("THREE_VOTER_QUORUM_STARTUP_FAILED") from None
                time.sleep(2)
        result["events"].append("three_voter_genesis_committed")
        for node in clients:
            other = EtcdQuorumFence(
                clients[node], base + "/active", cluster_id)
            assert other.snapshot().document["owner"] == "gateway-old"
        result["events"].append("same_raft_cluster_verified_across_three")
        # Optional REAL Neo4j admission on the same three-voter consensus
        # group. Requires a second manual opt-in; baseline remains unchanged.
        if os.getenv("ASSISTX_QUORUM_NEO4J_RESEARCH") == "1":
            from probe_trace_quorum_neo4j_physical import run_real_quorum_graph
            result["graph_integration"] = run_real_quorum_graph(
                clients, cluster_id, base, run_id, ip_map, client_port,
                certs, shell, container_names, started, result)

        # Explicit client-authentication negative: CA trust without a client
        # certificate MUST be insufficient to issue a Raft KV read.
        unauthenticated = ssl.create_default_context(
            cafile=str(certs / "client" / "ca.pem"))
        unauthenticated_req = Request(
            f"https://{ip_map['r1']}:{client_port}/v3/kv/range",
            data=canon({"key": b64(base + "/active")}),
            method="POST", headers={"Content-Type": "application/json"})
        try:
            with urlopen(unauthenticated_req, context=unauthenticated,
                         timeout=3) as response:
                response.read()
        except (HTTPError, URLError, OSError, TimeoutError):
            result["events"].append("no_client_certificate_denied")
        else:
            raise AssertionError("MTLS_UNAUTHENTICATED_CLIENT_ACCEPTED")

        attempt = EtcdQuorumFence(
            clients["r2"], base + "/active", cluster_id
        ).admit("gateway-old", 1, "pending-long-running-graph")
        assert len(attempt) == 32
        try:
            EtcdQuorumFence(clients["r3"], base + "/active", cluster_id).admit(
                "gateway-old", 1, "would-overadmit")
        except FenceRefused as exc:
            assert str(exc) == "PHYSICAL_CAPACITY_OCCUPIED"
        else:
            raise AssertionError("QUORUM_OVERADMITTED")
        result["events"].append("cap_one_global_across_endpoint")

        # Identify the CURRENT Raft leader from member_id/leader, then
        # actually stop the leader (not an arbitrary follower). Two voters
        # must elect a new leader while retaining the original intent.
        statuses = {
            node: client._post("/v3/maintenance/status", {})
            for node, client in clients.items()
        }
        leader_id = str(statuses["r1"]["leader"])
        leader_node = next(
            node for node, status in statuses.items()
            if str(status["header"]["member_id"]) == leader_id
        )
        leader_host = dict(HOSTS)[leader_node]
        before_raft_term = int(statuses["r1"]["raftTerm"])
        shell(["docker", "stop", "--time", "2",
               container_names[leader_host]], leader_host, timeout=16)
        started.remove(leader_host)
        survivor = next(node for node in clients if node != leader_node)
        still = EtcdQuorumFence(clients[survivor], base + "/active", cluster_id)
        deadline = time.monotonic() + 35
        while True:
            try:
                assert still.snapshot().document["pending"].get(attempt)
                break
            except (FenceRefused, AssertionError):
                if time.monotonic() > deadline:
                    raise RuntimeError("MAJORITY_FAILED_TO_ELECT")
                time.sleep(2)
        status_after = clients[survivor]._post("/v3/maintenance/status", {})
        assert int(status_after["raftTerm"]) > before_raft_term
        result["events"].append("real_raft_leader_re_election_observed")
        try:
            still.takeover("gateway-successor", {}, b"")
        except FenceRefused as exc:
            assert str(exc) == "UNCERTAIN_PHYSICAL_WORK_BLOCKS_TAKEOVER"
        else:
            raise AssertionError("TAKEOVER_WITH_PHYSICAL_WORK")
        result["events"].append("two_voter_majority_kept_state_and_blocked_takeover")

        # No old physical attempts in a DIFFERENT throwaway namespace. This
        # tests a signed control-plane transition without inventing closure.
        clean = EtcdQuorumFence(
            clients[survivor], base + "/empty-control", cluster_id)
        clean.bootstrap("empty-genesis-" + run_id, "gateway-old",
                        pubkey(operator), pubkey(witness), 1)
        current = clean.snapshot()
        approval = takeover_request(current, "gateway-successor")
        assert clean.takeover(
            "gateway-successor", approval, operator.sign(canon(approval))) == 2
        try:
            clean.admit("gateway-old", 1, "stale-owner-after-takeover")
        except FenceRefused as exc:
            assert str(exc) == "STALE_TERM_OR_OWNER"
        else:
            raise AssertionError("OLD_OWNER_ACCEPTED")
        result["events"].append("operator_signed_empty_takeover_term_2_old_denied")

        shell(["docker", "start", container_names[leader_host]],
              leader_host, timeout=20)
        started.add(leader_host)
        deadline = time.monotonic() + 40
        while True:
            try:
                assert EtcdQuorumFence(
                    clients[leader_node], base + "/empty-control",
                    cluster_id).snapshot().document["term"] == 2
                break
            except (FenceRefused, AssertionError):
                if time.monotonic() > deadline:
                    raise RuntimeError("RESTARTED_VOTER_NOT_RECOVERED")
                time.sleep(2)
        result["events"].append("rejoined_voter_preserved_term_2")

        # Now stop two voters and ask the lone survivor for a LINEARIZABLE
        # Range and a CAS write. Failure must never be interpreted as an
        # available capacity/free lease.
        for host in ("xwing", "destroyer"):
            shell(["docker", "stop", "--time", "2", container_names[host]],
                  host, timeout=20)
            started.remove(host)
        isolated = EtcdQuorumFence(
            clients["r1"], base + "/empty-control", cluster_id)
        try:
            isolated.snapshot()
        except FenceRefused:
            result["events"].append("quorum_loss_read_denied")
        else:
            raise AssertionError("MINORITY_LINEARIZABLE_READ_SUCCEEDED")
        # Try a DIRECT raw etcd Txn write, not only an application's
        # read-before-write path. This must also fail with one voter.
        canary_key = base + "/minority-write-must-not-commit"
        try:
            clients["r1"].txn({
                "compare": [{"key": b64(canary_key),
                             "target": "VERSION", "result": "EQUAL",
                             "version": "0"}],
                "success": [{"request_put": {
                    "key": b64(canary_key), "value": b64("UNSAFE")}}],
                "failure": []
            })
        except FenceRefused:
            result["events"].append("quorum_loss_write_denied")
        else:
            raise AssertionError("MINORITY_RAFT_TXN_WRITE_SUCCEEDED")
        # Restore both voters; no writes should have slipped in during loss.
        for host in ("xwing", "destroyer"):
            shell(["docker", "start", container_names[host]], host,
                  timeout=22)
            started.add(host)
        deadline = time.monotonic() + 55
        while True:
            try:
                recovered = isolated.snapshot()
                if recovered.document["term"] == 2:
                    assert not recovered.document["pending"]
                    break
            except FenceRefused:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("QUORUM_REJOIN_FAILED")
            time.sleep(2)
        assert not clients["r1"].range(canary_key).get("kvs")
        result["events"].append("recovered_quorum_preserved_term_and_no_minority_write")
        # Original pending attempt was never released by lease/leader change.
        assert gateway.snapshot().document["pending"].get(attempt)
        result["events"].append("original_graph_reservation_survived_all_partitions")
        # RBAC is separately opted-in: after all physical consensus tests,
        # enable exact-key auth on this ephemeral cluster and verify the
        # JSON-gateway bearer boundary before the scoped teardown.
        if os.getenv("ASSISTX_RAFT_RBAC_RESEARCH") == "1":
            from probe_trace_etcd_rbac_physical import run_scoped_rbac
            result["rbac_integration"] = run_scoped_rbac(
                clients, cluster_id, base, certs, ip_map["r1"], client_port)
            result["events"].append(
                "real_three_host_rbac_reader_denied_raw_authority_txn")

        result.update({
            "three_voters_on_distinct_hosts": True,
            "mtls_client_and_peer_auth": True,
            "real_raft_majority_survives_one_stop": True,
            "actual_leader_reelection_observed": True,
            "unauthenticated_tls_client_denied": True,
            "direct_minority_etcd_txn_failed_closed": True,
            "empty_namespace_signed_takeover_term_2": True,
            "stale_old_owner_rejected": True,
            "minority_read_denied": True,
            "minority_write_denied": True,
            "quorum_restart_recovery": True,
            "unresolved_graph_intent_never_freed": True,
        })
        return result
    finally:
        # Exact synthetic container/volume names only; do NOT alter host
        # services, Docker networks, Tailscale or privileged configs.
        for _, host in reversed(HOSTS):
            name = container_names.get(host)
            if name:
                try:
                    shell(["docker", "rm", "-f", "-v", name], host,
                          timeout=22, check=False)
                except Exception:
                    result["events"].append("cleanup_container_FAILED:" + host)
        for host in stage_hosts:
            try:
                shell(["python3", "-c",
                       "import shutil,sys;shutil.rmtree(sys.argv[1])",
                       str(staging)], host, timeout=12)
            except Exception:
                result["events"].append("cleanup_cert_FAILED:" + host)
        # Do not leave CA private key/credentials in a repository worktree.
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
