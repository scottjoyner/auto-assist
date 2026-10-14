"""Offline dual Ed25519 pinned-trust + external-anchor negative acceptance."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import json
import shutil
import time
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import trace_b01_signed_reservation_gate as mod
from trace_b01_signed_reservation_gate import (
    SCHEMA, SignedEnvelope, SignedResourceGate, AdmissionDenied,
    canonical, verify_envelope, proof_digest,
)

NOW=1855000000
def pub(key):
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)

@pytest.fixture
def fixture(tmp_path):
    issuer=Ed25519PrivateKey.generate()
    device=Ed25519PrivateKey.generate()
    db=tmp_path/"resource"/"ledger.sqlite"
    anchor=tmp_path/"anchor"/"state.json"
    db.parent.mkdir()
    anchor.parent.mkdir()
    gate=SignedResourceGate.bootstrap(db,anchor,pub(issuer),pub(device),
        "fixture-task-alpha123","fixture-agent-alpha123","a"*64)
    return gate,issuer,device
def signed(fixture, *,term=1,sequence=1,nonce="nonce-alpha123",
           issuer_override=None,device_override=None,**changes):
    gate,issuer,device=fixture
    body=dict(schema=SCHEMA,instance=gate.snapshot()["instance"],
        scope="fixture-assistx-b01",operation="claim_fixture_task",
        task_id="fixture-task-alpha123",agent_id="fixture-agent-alpha123",
        term=term,sequence=sequence,nonce=nonce,policy_sha256="a"*64,
        issued_at=NOW-5,expires_at=NOW+30)
    body.update(changes)
    data=canonical(body)
    signer1=issuer_override or issuer
    signer2=device_override or device
    return SignedEnvelope(body,signer1.sign(data),signer2.sign(data))

def test_dual_signed_positive_is_durable_pending_and_no_write_authority(fixture):
    gate,*_=fixture
    proof=signed(fixture)
    assert verify_envelope(proof,gate.issuer_root,gate.device_root,now=NOW) == proof_digest(proof)
    ticket=gate.reserve(proof,now=NOW)
    assert ticket["state"]=="PENDING_UNCERTAIN"
    assert ticket["proof_sha256"]==proof_digest(proof)
    assert ticket["graph_write_authorized"] is False
    assert ticket["production_authority"] is False
    new=SignedResourceGate(gate.db,gate.anchor,gate.issuer_root,gate.device_root)
    assert new.read_only_reconcile()=={
        "state":"PENDING_UNCERTAIN","high_water":1,"term":1,
        "automatic_reexecution_permitted":False,
        "automatic_failover_permitted":False}
@pytest.mark.parametrize("edit",[
    dict(task_id="fixture-task-foreign123"),
    dict(agent_id="fixture-agent-foreign123"),
    dict(policy_sha256="b"*64),
    dict(scope="fixture-foreign"),
    dict(operation="arbitrary_write"),
    dict(schema="wrong-schema"),
    dict(term=0),dict(sequence=0),dict(nonce="bad"),
    dict(issued_at=NOW+1),dict(expires_at=NOW-1),
    dict(issued_at=NOW-61,expires_at=NOW+30),
    dict(extra_role="superuser"),
])
def test_signed_but_unbound_or_bad_fields_deny(edit,fixture):
    gate,*_=fixture
    env=signed(fixture,**edit)
    with pytest.raises(AdmissionDenied):
        gate.reserve(env,now=NOW)
    assert gate.snapshot()["high_water"]==0

def test_wrong_root_signer_and_unsigned_digest_both_deny(fixture):
    gate,*_=fixture
    forged_issuer=signed(fixture,issuer_override=Ed25519PrivateKey.generate())
    forged_device=signed(fixture,device_override=Ed25519PrivateKey.generate())
    valid=signed(fixture)
    corrupted=replace(valid,issuer_signature=bytes(64))
    unauthorized=replace(valid,device_signature=bytes(64))
    for proof in (forged_issuer,forged_device,corrupted,unauthorized):
        with pytest.raises(AdmissionDenied,match="INVALID_PINNED_DUAL_SIGNATURE"):
            gate.reserve(proof,now=NOW)
    assert gate.snapshot()["pending"] is None
def test_tamper_after_signing_and_swapped_signer_roles_fail(fixture):
    gate,*_=fixture
    proof=signed(fixture)
    bad=SignedEnvelope({**proof.body,"sequence":2},
                       proof.issuer_signature,proof.device_signature)
    swapped=replace(proof,issuer_signature=proof.device_signature,
                    device_signature=proof.issuer_signature)
    for item in (bad,swapped):
        with pytest.raises(AdmissionDenied,match="INVALID_PINNED_DUAL_SIGNATURE"):
            gate.reserve(item,now=NOW)

def test_stale_and_forked_sequence_fail_even_with_valid_signatures(fixture):
    gate,*_=fixture
    for item in (
        signed(fixture,term=2),
        signed(fixture,sequence=2),
        signed(fixture,term=3,sequence=4),
    ):
        with pytest.raises(AdmissionDenied):
            gate.reserve(item,now=NOW)
    assert gate.snapshot()["high_water"]==0

def test_pending_blocks_replayed_signed_proof_and_new_nonce(fixture):
    gate,*_=fixture
    one=signed(fixture)
    gate.reserve(one,now=NOW)
    for next_proof in (
        one,
        signed(fixture,nonce="nonce-second123"),
        signed(fixture,sequence=2,nonce="nonce-third123"),
    ):
        with pytest.raises(AdmissionDenied,match="UNCERTAIN_INFLIGHT_CLAIM_BLOCKED"):
            gate.reserve(next_proof,now=NOW)
    assert gate.snapshot()["high_water"]==1
def test_different_pinned_roots_cannot_reopen_stored_state(fixture):
    gate,*_=fixture
    replacement=pub(Ed25519PrivateKey.generate())
    with pytest.raises(AdmissionDenied,match="PINNED_ROOT_OR_SCHEMA_MISMATCH"):
        SignedResourceGate(gate.db,gate.anchor,replacement,gate.device_root)
    with pytest.raises(AdmissionDenied,match="INVALID_DISTINCT_TRUST_ROOTS"):
        SignedResourceGate(gate.db,gate.anchor,gate.issuer_root,gate.issuer_root)

def test_graph_only_rollback_cannot_replay_against_anchor(fixture):
    gate,*_=fixture
    old=signed(fixture)
    gate.reserve(old,now=NOW)
    # A graph-only snapshot restore cannot rewind the separately stored state.
    # Actual graph restore is independently tested by the physical probe.
    assert gate.snapshot()["high_water"]==1
    with pytest.raises(AdmissionDenied):
        gate.reserve(old,now=NOW)

def test_db_only_rollback_detected_and_fails_closed(fixture,tmp_path):
    gate,*_=fixture
    baseline=tmp_path/"db-before"
    shutil.copy2(gate.db,baseline)
    gate.reserve(signed(fixture),now=NOW)
    shutil.copy2(baseline,gate.db)
    with pytest.raises(AdmissionDenied,match="ROLLBACK_OR_UNCERTAIN"):
        gate.snapshot()
    with pytest.raises(AdmissionDenied,match="ROLLBACK_OR_UNCERTAIN"):
        gate.reserve(signed(fixture),now=NOW)
def test_crash_after_anchor_before_db_commit_permanently_blocks(fixture,monkeypatch):
    gate,*_=fixture
    anchor_write=mod._atomic_anchor
    def fail_after_anchor(path,doc):
        anchor_write(path,doc)
        raise RuntimeError("SYNTHETIC_POWER_LOSS_AFTER_ANCHOR")
    monkeypatch.setattr(mod,"_atomic_anchor",fail_after_anchor)
    with pytest.raises(RuntimeError,match="POWER_LOSS"):
        gate.reserve(signed(fixture),now=NOW)
    with pytest.raises(AdmissionDenied,match="ROLLBACK_OR_UNCERTAIN"):
        gate.snapshot()

def test_concurrent_same_sequence_one_reserved_other_denied(fixture):
    gate,*_=fixture
    claims=(signed(fixture,nonce="nonce-threadone123"),
            signed(fixture,nonce="nonce-threadtwo123"))
    def try_it(c):
        try:
            return gate.reserve(c,now=NOW)
        except AdmissionDenied:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        decisions=list(pool.map(try_it,claims))
    assert sum(d is not None for d in decisions)==1
    assert gate.snapshot()["high_water"]==1

def test_co_restore_is_not_secure_rollback_resistance(fixture,tmp_path):
    gate,*_=fixture
    db_before=tmp_path/"db-before"
    anchor_before=tmp_path/"anchor-before"
    shutil.copy2(gate.db,db_before)
    shutil.copy2(gate.anchor,anchor_before)
    proof=signed(fixture)
    gate.reserve(proof,now=NOW)
    # A host operator controlling *both* copies can restore both: NO-GO.
    shutil.copy2(db_before,gate.db)
    shutil.copy2(anchor_before,gate.anchor)
    assert gate.snapshot()["high_water"]==0
    assert gate.reserve(proof,now=NOW)["state"]=="PENDING_UNCERTAIN"
def test_no_caller_selected_root_and_no_runtime_graph_connector():
    code=(Path(__file__).with_name(
        "trace_b01_signed_reservation_gate.py")).read_text()
    for bad in ("GraphDatabase.driver","import neo4j", "docker exec",
                "app.post(","os.getenv(", "sqlite3.connect(':memory:')"):
        assert bad not in code
    assert "Ed25519PublicKey.from_public_bytes" in code
    assert "BEGIN IMMEDIATE" in code
    assert "os.fsync" in code
    assert "UNCERTAIN_INFLIGHT_CLAIM_BLOCKED" in code

def test_physical_disposable_probe_is_opt_in_and_has_no_host_ports():
    src=Path(__file__).with_name(
        'probe_trace_b01_signed_resource_gate_physical.py').read_text()
    for must in (
        'EXPLICIT_SIGNED_DISPOSABLE_OPT_IN_REQUIRED',
        '"--internal","--driver","bridge"',
        '"--pull","never"',
        '"--memory","2300m"',
        '"--pids-limit","256"',
        '"--network",network',
        '"--env","NEO4J_AUTH=neo4j/"+admin',
        '"HostConfig"].get("PortBindings")',
        'docker("rm","-f","-v",graph',
        'docker("network","rm",network',
        'coordinated_restore_not_defended',
        'arbitrary_writer_bypass_still_reproduced',
        'neo4j_native_signature_enforcement_proven',
        'production_admission_authorized',
    ):
        assert must in src
    for unsafe in ('docker network disconnect',
                   'docker restart','docker stop',
                   'Tailscale', 'NEO4J_PASSWORD',
                   '/NAS5/', 'assistx-api'):
        assert unsafe not in src


def test_unknown_signature_data_does_not_bypass_validation(fixture):
    gate,*_=fixture
    valid=signed(fixture)
    bogus=SignedEnvelope({**valid.body, 'claimed_digest':'c'*64},
                         valid.issuer_signature,valid.device_signature)
    with pytest.raises(AdmissionDenied,match='MALFORMED_OR_UNPINNED_PROOF'):
        gate.reserve(bogus,now=NOW)
    assert gate.snapshot()['pending'] is None