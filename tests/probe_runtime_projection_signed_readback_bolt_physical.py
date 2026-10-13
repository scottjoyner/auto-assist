"""Explicit opt-in physical synthetic Neo4j graph readback + third signer.

All writes target disposable graph only. Production graph, host ports, mounts,
configured creds and network are never used. Readback actor gets MATCH only.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tempfile import TemporaryDirectory

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from probe_runtime_projection_broker_rbac_physical import (
    IMAGE,
    assert_disposable_container,
    docker,
    query,
)
from runtime_projection_offline_admission_ledger import (
    OPT_IN,
    AdmissionHold,
    initialize,
    inspection,
    reserve_verified_intent,
)
from runtime_projection_signed_graph_readback_research import (
    WITNESS_AUDIENCE,
    reconcile_synthetic_readback,
)
from test_runtime_projection_signed_apply_gate import (
    NOW,
    G,
    manifest,
    package,
    public,
)

PHYSICAL_OPT_IN="ASSISTX_P0_DISPOSABLE_SIGNED_READBACK"


def run():
    if os.getenv(PHYSICAL_OPT_IN)!="1":
        raise RuntimeError("EXPLICIT_DISPOSABLE_SIGNED_READBACK_REQUIRED")
    if os.getenv(OPT_IN)!="1":
        raise RuntimeError("EXPLICIT_DISPOSABLE_LEDGER_REQUIRED")
    docker("image","inspect",IMAGE,timeout=10)
    suffix=secrets.token_hex(6)
    net="assistx-p0-witness-only-"+suffix
    box="assistx-p0-neo-witness-"+suffix
    admin_pw="X"+secrets.token_urlsafe(32)
    witness_pw="X"+secrets.token_urlsafe(32)
    network_created=False
    container_created=False
    t0=time.monotonic()
    try:
        docker("network","create","--internal","--driver","bridge",net)
        network_created=True
        docker("run","-d","--pull","never","--name",box,
               "--network",net,"--memory","2300m","--cpus","1.5",
               "--pids-limit","256",
               "--env","NEO4J_AUTH=neo4j/"+admin_pw,
               "--env","NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
               "--env","NEO4J_server_memory_heap_initial__size=256m",
               "--env","NEO4J_server_memory_heap_max__size=512m",
               IMAGE,timeout=30)
        container_created=True
        uri=assert_disposable_container(box,net)
        source,reviewer,observer=[Ed25519PrivateKey.generate() for _ in range(3)]
        m=manifest()
        att=package(m,source,reviewer)
        digest=hashlib.sha256(G.canonical(att["source"])).hexdigest()
        with TemporaryDirectory(prefix="p0-readback-",dir="/tmp") as temp:
            ledger=Path(temp)/"state.sqlite3"
            initialize(ledger,trusted_genesis_generation=6)
            reserve_verified_intent(
                ledger,manifest=m,attestation=att,
                pinned_source_public_key=public(source),
                pinned_reviewer_public_key=public(reviewer),
                observed_now_ms=NOW)
            with GraphDatabase.driver(
                    uri,auth=("neo4j",admin_pw),connection_timeout=3) as admin:
                deadline=time.monotonic()+75
                while True:
                    try:
                        admin.verify_connectivity()
                        if query(admin,"RETURN 1 AS ok")==[{"ok":1}]:
                            break
                    except (ServiceUnavailable,Neo4jError,OSError):
                        pass
                    if time.monotonic()>deadline:
                        raise RuntimeError("DISPOSABLE_GRAPH_NOT_READY")
                    time.sleep(2)
                # Fixture permissions: observer can only read; no graph writes.
                admin_statements=[
                    "CREATE ROLE fixture_observer IF NOT EXISTS",
                    "GRANT ACCESS ON DATABASE neo4j TO fixture_observer",
                    "GRANT MATCH {name,generation,status,updated_at_ts,expires_at_ts} "
                    "ON GRAPH neo4j NODES FleetProjectionState TO fixture_observer",
                    "GRANT MATCH {approval_id,generation,source_receipt_sha256} "
                    "ON GRAPH neo4j NODES RuntimeProjectionApprovalReceipt "
                    "TO fixture_observer",
                    "CREATE USER fixture_observer SET PASSWORD '"+witness_pw+
                    "' CHANGE NOT REQUIRED",
                    "GRANT ROLE fixture_observer TO fixture_observer",
                ]
                for q in admin_statements:
                    query(admin,q,db="system")
                query(admin,"CREATE (:FleetProjectionState {"
                      "name:'canonical',generation:7,status:'approved',"
                      "updated_at_ts:$updated,expires_at_ts:$expiry})",
                      params={"updated":NOW-1000,"expiry":NOW+119000})
                # Synthetic compromised broker writes bad raw receipt material.
                query(admin,"CREATE (:RuntimeProjectionApprovalReceipt {"
                      "approval_id:$aid,generation:7,"
                      "source_receipt_sha256:'UNSIGNED_DIGEST'})",
                      params={"aid":m["approval_id"]})
                with GraphDatabase.driver(
                        uri,auth=("fixture_observer",witness_pw),
                        connection_timeout=3) as reader:
                    try:
                        query(reader,"MATCH (s:FleetProjectionState "
                              "{name:'canonical'}) SET s.expires_at_ts=1")
                    except Neo4jError:
                        write_denied=True
                    else:
                        raise RuntimeError("OBSERVER_CAN_WRITE")
                    def graph_claim():
                        state=query(reader,
                            "MATCH (s:FleetProjectionState {name:'canonical'}) "
                            "RETURN count(s) AS n, max(s.generation) AS gen,"
                            "max(s.status) AS status,"
                            "max(s.updated_at_ts) AS updated,"
                            "max(s.expires_at_ts) AS expiry")[0]
                        receipt=query(reader,
                            "MATCH (r:RuntimeProjectionApprovalReceipt "
                            "{approval_id:$aid}) RETURN count(r) AS n,"
                            "max(r.generation) AS gen,"
                            "max(r.source_receipt_sha256) AS digest",
                            params={"aid":m["approval_id"]})[0]
                        payload={
                            "audience":WITNESS_AUDIENCE,
                            "role":"independent-graph-observer",
                            "manifest_sha256":hashlib.sha256(
                                G.canonical(m)).hexdigest(),
                            "source_receipt_sha256":digest,
                            "approval_id":m["approval_id"],
                            "generation":7,"previous_generation":6,
                            "canonical_generation":state["gen"],
                            "canonical_status":state["status"],
                            "canonical_updated_at_ms":state["updated"],
                            "canonical_expires_at_ms":state["expiry"],
                            "matching_canonical_rows":state["n"],
                            "matching_approval_receipts":receipt["n"],
                            "receipt_generation":receipt["gen"],
                            "receipt_source_receipt_sha256":receipt["digest"],
                            "observed_at_ms":NOW-500,
                            "expires_at_ms":NOW+25000,
                        }
                        return {"payload":payload,
                                "signature":observer.sign(G.canonical(payload)).hex()}
                    params=dict(
                        db_path=ledger,manifest=m,attestation=att,
                        pinned_source_key=public(source),
                        pinned_reviewer_key=public(reviewer),
                        pinned_observer_key=public(observer),trusted_now_ms=NOW)
                    try:
                        reconcile_synthetic_readback(
                            **params,sealed_readback=graph_claim())
                    except AdmissionHold:
                        wrong_digest_denied=True
                    else:
                        raise RuntimeError("UNSIGNED_DIGEST_ACCEPTED")
                    if inspection(ledger)["last_confirmed_generation"]!=6:
                        raise RuntimeError("HIGH_WATER_CHANGED_ON_INVALID")
                    query(admin,"MATCH (r:RuntimeProjectionApprovalReceipt "
                          "{approval_id:$aid}) SET r.source_receipt_sha256=$sha",
                          params={"aid":m["approval_id"],"sha":digest})
                    sealed=graph_claim()
                    accepted=reconcile_synthetic_readback(
                        **params,sealed_readback=sealed)
                    if accepted.generation!=7 or not accepted.pending_cleared_in_offline_ledger:
                        raise RuntimeError("VALID_READBACK_NOT_ACCEPTED")
                    try:
                        reconcile_synthetic_readback(**params,sealed_readback=sealed)
                    except AdmissionHold:
                        replay_denied=True
                    else:
                        raise RuntimeError("WITNESS_REPLAY_ACCEPTED")
                    if inspection(ledger)["last_confirmed_generation"]!=7:
                        raise RuntimeError("OFFLINE_HIGH_WATER_NOT_ADVANCED")
            return {
                "schema":"assistx-p0-independent-sealed-graph-readback-physical-v1",
                "result":"PASS_SYNTHETIC_GRAPH_READBACK_ONLY",
                "disposable_image":IMAGE,
                "host_ports_published":False,
                "host_persistent_mounts":False,
                "internal_only_network":True,
                "read_only_observer_write_denied":write_denied,
                "wrong_digest_signed_readback_denied":wrong_digest_denied,
                "triple_signed_synthetic_readback_acceptance":True,
                "duplicate_witness_replay_denied":replay_denied,
                "source_and_review_independent_keys":True,
                "production_commit_authority_proven":False,
                "production_graph_effects":False,
                "production_admission_authorized":False,
                "elapsed_seconds":round(time.monotonic()-t0,2),
            }
    finally:
        if container_created:
            try:
                docker("rm","-fv",box,timeout=20)
            except Exception:
                pass
        if network_created:
            try:
                docker("network","rm",net,timeout=20)
            except Exception:
                pass


if __name__=="__main__":
    try:
        print(json.dumps(run(),sort_keys=True))
    except Exception as exc:
        print(json.dumps({"schema":"assistx-p0-independent-sealed-graph-readback-physical-v1",
                          "result":"UNVERIFIED","failure_class":type(exc).__name__,
                          "production_admission_authorized":False},sort_keys=True))
        raise SystemExit(2) from None
