"""Opt-in physical etcd RBAC acceptance on the *same disposable three-host cluster*.

Invoked only AFTER all leader/quorum/graph experiments, immediately before
teardown. Enables etcd auth on that ephemeral cluster, NEVER production.
Uses JSON gRPC-gateway bearer tokens; TLS client CN alone is not an identity
under this gateway. No passwords or bearer tokens are written to evidence.
"""
from __future__ import annotations

import json
import secrets

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from trace_etcd_quorum_fence_research import (
    EtcdQuorumFence, EtcdTLS, FenceRefused, b64, canon
)
from trace_etcd_bearer_rbac_research import EtcdBearerTLS


def run_scoped_rbac(clients, cluster_id, base, certs, ip, client_port):
    transport = clients["r1"]
    isolated_key = base + "/rbac-readonly-gateway-authority"
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

    # Prepare genuine etcd v3 users and exact-key roles BEFORE enabling auth.
    # Unique disposable cluster, no existing production root/users/roles.
    root_password = secrets.token_urlsafe(36)
    reader_password = secrets.token_urlsafe(36)
    writer_password = secrets.token_urlsafe(36)
    for path, payload in [
        ("/v3/auth/user/add",
         {"name": "root", "password": root_password}),
        ("/v3/auth/role/add", {"name": "root"}),
        ("/v3/auth/user/grant", {"user": "root", "role": "root"}),
        ("/v3/auth/role/add", {"name": "gateway-read"}),
        ("/v3/auth/role/add", {"name": "policy-write"}),
        ("/v3/auth/role/grant", {
            "name": "gateway-read",
            "perm": {"permType": "READ", "key": b64(isolated_key)}
        }),
        ("/v3/auth/role/grant", {
            "name": "policy-write",
            "perm": {"permType": "READWRITE", "key": b64(isolated_key)}
        }),
        ("/v3/auth/user/add", {
            "name": "gateway-reader", "password": reader_password
        }),
        ("/v3/auth/user/add", {
            "name": "authority-policy", "password": writer_password
        }),
        ("/v3/auth/user/grant", {
            "user": "gateway-reader", "role": "gateway-read"
        }),
        ("/v3/auth/user/grant", {
            "user": "authority-policy", "role": "policy-write"
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

    paths = tuple(str(certs / "client" / name)
                  for name in ("ca.pem", "node.pem", "node-key.pem"))
    address = f"https://{ip}:{client_port}"

    def login(name, password):
        # Diagnostic surface redacts credentials and authorization tokens.
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError, URLError
        request = Request(
            transport.endpoint + "/v3/auth/authenticate",
            data=canon({"name": name, "password": password}),
            headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urlopen(request, timeout=3, context=transport.context) as reply:
                response = json.loads(reply.read(2048))
        except HTTPError as exc:
            info = {}
            try:
                info = json.loads(exc.read(1024))
            except Exception:
                pass
            message = str(info.get("message", "unknown"))
            for secret in (root_password, reader_password, writer_password):
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
        "enforcing_policy_service_not_implemented": True,
        "production_authority": False,
    }
