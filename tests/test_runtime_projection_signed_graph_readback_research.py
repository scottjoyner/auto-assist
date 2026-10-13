"""Synthetic triple-signed graph witness and durable ledger high-water tests."""
from __future__ import annotations

import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
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
    verify_sealed_readback,
)
from test_runtime_projection_signed_apply_gate import (
    NOW,
    G,
    manifest,
    package,
    public,
)


@pytest.fixture
def scene(monkeypatch, tmp_path):
    monkeypatch.setenv(OPT_IN, "1")
    source, reviewer, observer = [Ed25519PrivateKey.generate() for _ in range(3)]
    m = manifest()
    att = package(m, source, reviewer)
    path = tmp_path / "signed-readback.sqlite3"
    initialize(path, trusted_genesis_generation=6)
    reserve_verified_intent(
        path, manifest=m, attestation=att,
        pinned_source_public_key=public(source),
        pinned_reviewer_public_key=public(reviewer), observed_now_ms=NOW)
    digest = hashlib.sha256(G.canonical(att["source"])).hexdigest()
    payload = {
        "audience":WITNESS_AUDIENCE,
        "role":"independent-graph-observer",
        "manifest_sha256":hashlib.sha256(G.canonical(m)).hexdigest(),
        "source_receipt_sha256":digest,
        "approval_id":m["approval_id"],
        "generation":7,"previous_generation":6,
        "canonical_generation":7,"canonical_status":"approved",
        "canonical_updated_at_ms":NOW-1000,
        "canonical_expires_at_ms":NOW+119000,
        "matching_canonical_rows":1,"matching_approval_receipts":1,
        "receipt_generation":7,
        "receipt_source_receipt_sha256":digest,
        "observed_at_ms":NOW-500,"expires_at_ms":NOW+25000,
    }
    return {
        "db_path":path,"manifest":m,"attestation":att,
        "pinned_source_key":public(source),
        "pinned_reviewer_key":public(reviewer),
        "pinned_observer_key":public(observer),
        "observer_private":observer,
        "trusted_now_ms":NOW,
        "payload":payload,
    }


def arguments(scene):
    return {k:v for k,v in scene.items() if k not in
            ("payload","observer_private")}


def signed(scene, altered=None):
    payload=copy.deepcopy(scene["payload"] if altered is None else altered)
    signature=scene["observer_private"].sign(G.canonical(payload)).hex()
    return {"payload":payload,"signature":signature}


def test_triple_signed_synthetic_readback_advances_offline_only(scene):
    outcome=reconcile_synthetic_readback(
        **arguments(scene),sealed_readback=signed(scene))
    assert outcome.generation==7
    assert outcome.pending_cleared_in_offline_ledger
    assert not outcome.production_graph_commit_proven
    assert not outcome.production_routing_authorized
    assert inspection(scene["db_path"]) == {
        "last_confirmed_generation":7,
        "unresolved_pending":False,
        "pending_generation":None,
        "replay_tombstone_count":1,
        "graph_write_authorized":False,
        "auto_reconciliation_authorized":False,
    }
    with pytest.raises(AdmissionHold,match="NO_EXACT_UNRESOLVED"):
        reconcile_synthetic_readback(**arguments(scene),sealed_readback=signed(scene))


@pytest.mark.parametrize("field,value",[
    ("audience","other"),("role","operator-reviewer"),
    ("manifest_sha256","0"*64),("source_receipt_sha256","0"*64),
    ("approval_id","different"),("generation",6),
    ("previous_generation",7),("canonical_generation",8),
    ("canonical_status","uninitialized"),
    ("matching_canonical_rows",2),("matching_approval_receipts",0),
    ("receipt_generation",6),("receipt_source_receipt_sha256","0"*64),
    ("canonical_expires_at_ms",NOW+86_400_000),
    ("canonical_updated_at_ms",NOW-3_600_000),
    ("observed_at_ms",NOW-32_000),
    ("observed_at_ms",NOW+10_000),
    ("expires_at_ms",NOW-1),
])
def test_even_valid_independent_signature_with_wrong_claim_stays_held(scene,field,value):
    payload=copy.deepcopy(scene["payload"])
    payload[field]=value
    with pytest.raises((AdmissionHold,ValueError)):
        reconcile_synthetic_readback(
            **arguments(scene),sealed_readback=signed(scene,payload))
    state=inspection(scene["db_path"])
    assert state["last_confirmed_generation"]==6
    assert state["unresolved_pending"] is True


def test_forged_observer_signature_and_unknown_extra_fields_denied(scene):
    forged=signed(scene)
    forged["signature"]="0"*128
    with pytest.raises(ValueError,match="signature"):
        reconcile_synthetic_readback(**arguments(scene),sealed_readback=forged)
    payload=copy.deepcopy(scene["payload"])
    payload["graph_full_record"]={"password":"x"}
    with pytest.raises(ValueError):
        reconcile_synthetic_readback(
            **arguments(scene),sealed_readback=signed(scene,payload))
    assert inspection(scene["db_path"])["unresolved_pending"]


def test_wrong_observer_key_and_same_source_key_denied(scene):
    with pytest.raises(ValueError,match="signature"):
        reconcile_synthetic_readback(
            **(arguments(scene)|{"pinned_observer_key":public(Ed25519PrivateKey.generate())}),
            sealed_readback=signed(scene))
    with pytest.raises(AdmissionHold,match="INDEPENDENT"):
        reconcile_synthetic_readback(
            **(arguments(scene)|{"pinned_observer_key":scene["pinned_source_key"]}),
            sealed_readback=signed(scene))
    assert inspection(scene["db_path"])["unresolved_pending"]


def test_unsigned_caller_claim_cannot_clear_pending(scene):
    fake={"payload":scene["payload"],"signature":None}
    with pytest.raises(ValueError):
        reconcile_synthetic_readback(**arguments(scene),sealed_readback=fake)
    assert inspection(scene["db_path"])["unresolved_pending"]


def test_double_concurrent_witness_can_only_advance_once(scene):
    claim=signed(scene)
    def attempt(_):
        try:
            reconcile_synthetic_readback(**arguments(scene),sealed_readback=claim)
            return "confirmed_offline"
        except AdmissionHold:
            return "held"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(attempt,range(8)))
    assert results.count("confirmed_offline")==1
    assert results.count("held")==7
    assert inspection(scene["db_path"])["last_confirmed_generation"]==7


def test_reopened_disposable_ledger_after_missing_witness_keeps_hold(scene):
    before=inspection(scene["db_path"])
    after=inspection(scene["db_path"])
    assert before==after
    assert before["last_confirmed_generation"]==6
    assert before["unresolved_pending"]
    assert before["replay_tombstone_count"]==1


def test_revalidation_of_expired_original_source_denied(scene):
    with pytest.raises(ValueError):
        reconcile_synthetic_readback(
            **(arguments(scene)|{"trusted_now_ms":NOW+400_000}),
            sealed_readback=signed(scene))
    assert inspection(scene["db_path"])["unresolved_pending"]


def test_no_graph_connection_or_signing_private_key_in_verifier():
    from pathlib import Path
    source=Path(__file__).with_name(
        "runtime_projection_signed_graph_readback_research.py").read_text()
    for forbidden in ("GraphDatabase.driver", "NEO4J_PASSWORD",
                      "Ed25519PrivateKey", "docker run", "app.post(",
                      "execute_write(", "session.run(", "GraphDatabase"):
        assert forbidden not in source
    assert "production_graph_commit_proven: bool = False" in source
