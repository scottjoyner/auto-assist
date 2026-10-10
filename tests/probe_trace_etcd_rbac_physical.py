"""Opt-in physical etcd RBAC acceptance on the *same disposable three-host cluster*.

Invoked only AFTER all leader/quorum/graph experiments, immediately before
teardown. Enables etcd auth on that ephemeral cluster, NEVER production.
Uses JSON gRPC-gateway bearer tokens; TLS client CN alone is not an identity
under this gateway. No passwords or bearer tokens are written to evidence.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import secrets
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from trace_etcd_quorum_fence_research import (
    EtcdQuorumFence, EtcdTLS, FenceRefused, b64, canon
)
from trace_etcd_bearer_rbac_research import EtcdBearerTLS


def _serve_real_rbac_policy(socket_path, endpoint, tls_paths, bearer,
                            keyspace, cluster_id, ready, allowed_uid):
    """The raw exact-key etcd writer token exists only in this service child."""
    from trace_etcd_policy_writer_research import (
        PolicyEngine, UnixPolicyWriterServer
    )
    authenticated_writer = EtcdBearerTLS(
        endpoint, *tls_paths, token=bearer)
    policy = PolicyEngine(
        EtcdQuorumFence(authenticated_writer, keyspace, cluster_id),
        "gateway-ipc", 1, "rbac-service-genesis",
        frozenset({"approved_read"})
    )
    server = UnixPolicyWriterServer(socket_path, policy, allowed_uid)
    ready.send("ready")
    try:
        server.serve_forever()
    finally:
        server.shutdown()


def run_scoped_rbac(clients, cluster_id, base, certs, ip, client_port):
    transport = clients["r1"]
    isolated_key = base + "/rbac-readonly-gateway-authority"
    service_key = base + "/rbac-ipc-policy-writer"
    operator = Ed25519PrivateKey.generate()
    witness = Ed25519PrivateKey.generate()
    public = lambda k: k.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    EtcdQuorumFence(
        transport, isolated_key, cluster_id
    ).bootstrap(
        "rbac-fixture-genesis", "authorized-service", public(operator),
        public(witness), capacity=1
    )
    EtcdQuorumFence(
        transport, service_key, cluster_id
    ).bootstrap(
        "rbac-service-genesis", "gateway-ipc", public(operator),
        public(witness), capacity=1
    )

    # Prepare genuine etcd v3 users and exact-key roles BEFORE enabling auth.
    # Unique disposable cluster, no existing production root/users/roles.
    root_password = secrets.token_urlsafe(36)
    reader_password = secrets.token_urlsafe(36)
    writer_password = secrets.token_urlsafe(36)
    service_password = secrets.token_urlsafe(36)
    for path, payload in [
        ("/v3/auth/user/add",
         {"name": "root", "password": root_password}),
        ("/v3/auth/role/add", {"name": "root"}),
        ("/v3/auth/user/grant", {"user": "root", "role": "root"}),
        ("/v3/auth/role/add", {"name": "gateway-read"}),
        ("/v3/auth/role/add", {"name": "policy-write"}),
        ("/v3/auth/role/add", {"name": "ipc-service-write"}),
        ("/v3/auth/role/grant", {
            "name": "gateway-read",
            "perm": {"permType": "READ", "key": b64(isolated_key)}
        }),
        ("/v3/auth/role/grant", {
            "name": "policy-write",
            "perm": {"permType": "READWRITE", "key": b64(isolated_key)}
        }),
        ("/v3/auth/role/grant", {
            "name": "ipc-service-write",
            "perm": {"permType": "READWRITE", "key": b64(service_key)}
        }),
        ("/v3/auth/user/add", {
            "name": "gateway-reader", "password": reader_password
        }),
        ("/v3/auth/user/add", {
            "name": "authority-policy", "password": writer_password
        }),
        ("/v3/auth/user/add", {
            "name": "ipc-service-policy", "password": service_password
        }),
        ("/v3/auth/user/grant", {
            "user": "gateway-reader", "role": "gateway-read"
        }),
        ("/v3/auth/user/grant", {
            "user": "authority-policy", "role": "policy-write"
        }),
        ("/v3/auth/user/grant", {
            "user": "ipc-service-policy", "role": "ipc-service-write"
        }),
    ]:
        transport._post(path, payload)
    transport._post("/v3/auth/enable", {})

    # IMPORTANT: JSON gRPC gateway does NOT authenticate via TLS Common Name.
    # A cert-only client must not retain any authority after auth enabled.
    try:
        transport.range(isolated_key)
    except FenceRefused:
        pass
    else:
        raise AssertionError("CERT_ONLY_ETCD_CLIENT_BYPASSED_RBAC")

    paths = tuple(str(certs / "json-client" / name)
                  for name in ("ca.pem", "node.pem", "node-key.pem"))
    address = f"https://{ip}:{client_port}"
    json_transport = EtcdTLS(address, *paths)

    def login(name, password):
        # Diagnostic surface redacts credentials and authorization tokens.
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError, URLError
        request = Request(
            json_transport.endpoint + "/v3/auth/authenticate",
            data=canon({"name": name, "password": password}),
            headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urlopen(request, timeout=3, context=json_transport.context) as reply:
                response = json.loads(reply.read(2048))
        except HTTPError as exc:
            info = {}
            raw = exc.read(1024).decode("utf-8", "replace")
            try:
                info = json.loads(raw)
            except Exception:
                pass
            message = str(info.get("message", raw[:150] or "unknown"))
            for secret in (root_password, reader_password,
                           writer_password, service_password):
                message = message.replace(secret, "REDACTED")
            raise RuntimeError(
                f"DISPOSABLE_RBAC_LOGIN_FAILED status={exc.code} message={message[:160]}"
            ) from None
        token = response.get("token")
        if not token:
            raise AssertionError("ETCD_AUTHENTICATE_MISSING_BEARER")
        return EtcdBearerTLS(address, *paths, token=token)

    reader = login("gateway-reader", reader_password)
    writer = login("authority-policy", writer_password)
    service_writer = login("ipc-service-policy", service_password)
    root = login("root", root_password)
    assert "REDACTED" in repr(reader) and reader_password not in repr(reader)

    read_auth = EtcdQuorumFence(reader, isolated_key, cluster_id)
    write_auth = EtcdQuorumFence(writer, isolated_key, cluster_id)
    assert read_auth.snapshot().document["owner"] == "authorized-service"
    assert write_auth.snapshot().document["term"] == 1

    # Reader may inspect one authorized key, but cannot write even through
    # the high-level CAS controller that a gateway might import.
    try:
        read_auth.admit("authorized-service", 1, "rogue-no-write")
    except FenceRefused:
        pass
    else:
        raise AssertionError("READER_MUTATED_RAFT_AUTHORITY")

    # An authenticated reader also may not read another authority key.
    try:
        reader.range(base + "/active")
    except FenceRefused:
        pass
    else:
        raise AssertionError("READER_ESCAPED_ASSIGNED_KEY_RANGE")

    # A policy writer may admit via CAS; reader still sees committed change.
    token = write_auth.admit("authorized-service", 1, "approved-policy-write")
    assert token in read_auth.snapshot().document["pending"]

    # Important residual: possession of policy-WRITER credentials allows
    # a raw KV CAS update without operator signature. The independently
    # authenticated writer must therefore be a tiny enforcing service.
    before = write_auth.snapshot()
    forged = json.loads(canon(before.document))
    forged["term"] = 77
    forged["owner"] = "malicious-authority-writer"
    response = writer.txn({
        "compare": [{
            "key": b64(isolated_key),
            "target": "MOD", "result": "EQUAL",
            "mod_revision": str(before.mod_revision)
        }],
        "success": [{
            "request_put": {
                "key": b64(isolated_key), "value": b64(canon(forged))
            }
        }],
        "failure": [],
    })
    assert response.get("succeeded") is True
    assert read_auth.snapshot().document["term"] == 77

    # Even a restricted writer cannot touch another key outside its grant.
    try:
        writer.range(base + "/active")
    except FenceRefused:
        pass
    else:
        raise AssertionError("POLICY_WRITER_ESCAPED_EXACT_KEY_SCOPE")

    # An actual child process now owns an independent exact-key bearer
    # writer credential. The IPC caller has ONLY a UNIX socket path, no raw
    # etcd credentials or direct KV API. The parent harness is still
    # privileged and both actors share the same UID: research limitation.
    from trace_etcd_policy_writer_research import (
        PolicyRefused, UnixPolicyClient, UnixPolicyGrantAdapter
    )
    ctx = mp.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="assistx-rbac-ipc-") as home:
        os.chmod(home, 0o700)
        sock_path = str(Path(home) / "authority.sock")
        parent, child = ctx.Pipe()
        service_process = ctx.Process(
            target=_serve_real_rbac_policy,
            args=(sock_path, address, paths, service_writer._token,
                  service_key, cluster_id, child, os.getuid())
        )
        service_process.start()
        try:
            assert parent.poll(12), "PHYSICAL_POLICY_WRITER_NOT_READY"
            assert parent.recv() == "ready"
            unix_client = UnixPolicyClient(sock_path)
            gateway = UnixPolicyGrantAdapter(unix_client, "rbac-service-genesis")
            assert unix_client.__dict__ == {"path": sock_path}
            with_asserted_denials = [
                {"kind": "raw-txn", "key": service_key, "value": "FORGED"},
                {"kind": "admit", "operation_id": "f" * 32,
                 "plan_id": "MATCH DELETE"},
                {"kind": "admit", "operation_id": "f" * 32,
                 "plan_id": "approved_read", "term": 999},
            ]
            for req in with_asserted_denials:
                try:
                    unix_client.request(req)
                except PolicyRefused:
                    pass
                else:
                    raise AssertionError("POLICY_WRITER_IPC_BYPASS")
            grant = gateway.admit("f" * 32, "approved_read")
            assert len(grant.reservation_id) == 32
            assert unix_client.request({"kind": "status"})["pending"] == 1
            # Administrative inspection is test-harness only, not gateway.
            actual = EtcdQuorumFence(root, service_key, cluster_id).snapshot()
            assert grant.reservation_id in actual.document["pending"]
            try:
                reader.range(service_key)
            except FenceRefused:
                pass
            else:
                raise AssertionError("GATEWAY_READER_ESCAPED_TO_WRITER_KEY")
        finally:
            service_process.terminate()
            service_process.join(5)
            if service_process.is_alive():
                service_process.kill()
                service_process.join(3)
            assert not service_process.is_alive()
        # Death of the *writer service* does not auto-free Raft capacity.
        assert grant.reservation_id in EtcdQuorumFence(
            root, service_key, cluster_id).snapshot().document["pending"]

    # Administrative root may see the protected key, but must not be given
    # to a worker/gateway or used as an ordinary policy-writer identity.
    assert EtcdQuorumFence(root, isolated_key, cluster_id).snapshot().document[
        "owner"] == "malicious-authority-writer"
    return {
        "real_three_host_etcd_rbac": True,
        "json_gateway_requires_bearer_not_tls_cn": True,
        "authenticated_exact_key_read_only": True,
        "read_only_credential_direct_txn_denied": True,
        "read_only_key_range_escape_denied": True,
        "separately_authenticated_policy_write": True,
        "writer_out_of_range_access_denied": True,
        "writer_raw_kv_policy_bypass_still_possible": True,
        "credentials_ephemeral_not_retained": True,
        "real_rbac_writer_process_unix_ipc_admission": True,
        "gateway_client_has_no_raw_kv_or_writer_token": True,
        "writer_process_death_preserves_raft_reservation": True,
        "writer_and_gateway_distinct_os_uid": False,
        "enforcing_policy_service_not_implemented": False,
        "production_policy_service_deployed": False,
        "production_authority": False,
    }
