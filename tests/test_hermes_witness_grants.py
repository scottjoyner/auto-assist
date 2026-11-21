"""Signed owner-state mutation grants; synthetic ephemeral signers only."""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.hermes_high_water import canonical
from assistx.hermes_witness_ledger import ResearchHighWaterLedger, LedgerDenied
from assistx.hermes_witness_grants import verify_owner_grant, InvalidGrant, EMPTY

OWNER = "hermes-test-owner"
CP1 = "1"*64
CP2 = "2"*64
M1 = "a"*64
M2 = "b"*64

@pytest.fixture
def db_path(tmp_path):
    base=tmp_path/"private-ledger"
    base.mkdir(mode=0o700)
    return str(base/"witness.sqlite")

@pytest.fixture
def signers():
    issuer=Ed25519PrivateKey.generate()
    responder=Ed25519PrivateKey.generate()
    pub=lambda s:s.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert pub(issuer)!=pub(responder)
    return issuer,pub(issuer),pub(responder)

def grant(issuer, *,action="publish",owner=OWNER,prior=0,
          prior_cp=EMPTY,prior_manifest=EMPTY,generation=1,
          cp=CP1,manifest=M1,nonce=None):
    body={"schema":1,"action":action,"owner":owner,
          "nonce":nonce or secrets.token_hex(32),
          "prior_generation":prior,
          "prior_checkpoint_sha256":prior_cp,
          "prior_manifest_sha256":prior_manifest,
          "generation":generation,"checkpoint_sha256":cp,
          "manifest_sha256":manifest}
    return canonical({**body,"signature":issuer.sign(canonical(body)).hex()})

def apply(db, raw, signers):
    return db.apply_signed_grant(raw_grant=raw,
        authorizer_public_key=signers[1],
        witness_signing_public_key=signers[2])

def gen2(signers,**kw):
    return grant(signers[0],prior=1,prior_cp=CP1,prior_manifest=M1,
                 generation=2,cp=CP2,manifest=M2,**kw)

def test_signed_authorizer_enrolls_exact_owner(db_path,signers):
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,grant(signers[0]),signers)
        assert db._get(OWNER)==(1,CP1,M1,0)

def test_signature_is_distinct_from_witness_signer(db_path,signers):
    with ResearchHighWaterLedger(db_path) as db:
        with pytest.raises(InvalidGrant):
            db.apply_signed_grant(raw_grant=grant(signers[0]),
                authorizer_public_key=signers[2],
                witness_signing_public_key=signers[2])
        assert db._get(OWNER) is None

def test_exact_prior_and_signed_next_generation(db_path,signers):
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,grant(signers[0]),signers)
        apply(db,gen2(signers),signers)
        assert db._get(OWNER)==(2,CP2,M2,0)

def test_signed_revoke_sticky_across_reopen(db_path,signers):
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,grant(signers[0]),signers)
        raw=grant(signers[0],action="revoke",prior=1,
                  prior_cp=CP1,prior_manifest=M1,generation=1)
        apply(db,raw,signers)
    with ResearchHighWaterLedger(db_path) as db:
        assert db._get(OWNER)==(1,CP1,M1,1)
        with pytest.raises(LedgerDenied,match="sticky"):
            apply(db,gen2(signers),signers)

def test_signed_nonce_replay_denied_even_for_identical_state(db_path,signers):
    raw=grant(signers[0])
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,raw,signers)
        with pytest.raises(LedgerDenied):
            apply(db,raw,signers)
        assert db._get(OWNER)==(1,CP1,M1,0)

@pytest.mark.parametrize("bad",[
    {"prior":2}, {"prior_cp":CP2}, {"prior_manifest":M2},
    {"prior":3}, {"prior_cp":"f"*64}, {"prior_manifest":"e"*64}
])
def test_conflicting_signed_predecessor_denied(db_path,signers,bad):
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,grant(signers[0]),signers)
        candidate=gen2(signers,**bad) if not (set(bad)&{"prior","prior_cp","prior_manifest"}) else grant(
            signers[0],prior=bad.get("prior",1),
            prior_cp=bad.get("prior_cp",CP1),
            prior_manifest=bad.get("prior_manifest",M1),
            generation=2,cp=CP2,manifest=M2)
        if candidate == gen2(signers):raise AssertionError("wrong fixture")
        with pytest.raises(LedgerDenied):
            apply(db,candidate,signers)

def test_same_generation_signed_equivocation_denied(db_path,signers):
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,grant(signers[0]),signers)
        same=grant(signers[0],prior=1,prior_cp=CP1,prior_manifest=M1,
                    generation=1,cp="3"*64,manifest=M1)
        with pytest.raises(LedgerDenied):
            apply(db,same,signers)

def test_old_signed_grant_replay_against_advanced_owner_denied(db_path,signers):
    older=gen2(signers)
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,grant(signers[0]),signers)
        apply(db,gen2(signers),signers)
        with pytest.raises(LedgerDenied):
            apply(db,older,signers)

def test_revoke_requires_exact_current_generation_and_hash(db_path,signers):
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,grant(signers[0]),signers)
        for raw in (
            grant(signers[0],action="revoke",generation=2,
                  prior=1,prior_cp=CP1,prior_manifest=M1),
            grant(signers[0],action="revoke",generation=1,
                  prior=1,prior_cp=CP1,prior_manifest=M1,cp=CP2),
        ):
            with pytest.raises(LedgerDenied):
                apply(db,raw,signers)
        assert db._get(OWNER)==(1,CP1,M1,0)

@pytest.mark.parametrize("mutation",[
    "signature","owner","action","generation","checkpoint_sha256",
    "manifest_sha256","prior_generation","prior_checkpoint_sha256",
    "prior_manifest_sha256","nonce","schema",
])
def test_forged_grant_field_denied(db_path,signers,mutation):
    signed=grant(signers[0])
    data=json.loads(signed)
    data[mutation] = {
        "signature": "0"*128, "owner":"another-owner",
        "action":"revoke","generation":3,"checkpoint_sha256":"3"*64,
        "manifest_sha256":"c"*64,"prior_generation":1,
        "prior_checkpoint_sha256":CP1,
        "prior_manifest_sha256":M1,"nonce":"f"*64,"schema":2
    }[mutation]
    with ResearchHighWaterLedger(db_path) as db:
        with pytest.raises(InvalidGrant):
            apply(db,canonical(data),signers)
        assert db._get(OWNER) is None

@pytest.mark.parametrize("bad",[
    b"", b"{}", b"[]", b"null", b"{unparseable",
    b"x"*4097
])
def test_missing_or_malformed_grants_denied(db_path,signers,bad):
    with ResearchHighWaterLedger(db_path) as db:
        with pytest.raises(InvalidGrant):
            apply(db,bad,signers)

@pytest.mark.parametrize("action",["unknown","PUB","",12])
def test_unrecognized_grant_actions_denied(signers,action):
    raw=grant(signers[0])
    obj=json.loads(raw);obj["action"]=action
    with pytest.raises(InvalidGrant):
        verify_owner_grant(canonical(obj),signers[1],signers[2])

def test_wrong_issuer_key_denied_without_owner_mutation(db_path,signers):
    wrong=Ed25519PrivateKey.generate()
    with ResearchHighWaterLedger(db_path) as db:
        with pytest.raises(InvalidGrant):
            apply(db,grant(wrong),signers)
        assert db._get(OWNER) is None

def test_other_owner_cannot_modify_signed_owner_state(db_path,signers):
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,grant(signers[0]),signers)
        apply(db,grant(signers[0],owner="second-owner"),signers)
        assert db._get(OWNER)==(1,CP1,M1,0)
        assert db._get("second-owner")==(1,CP1,M1,0)

def test_grant_nonce_is_unique_across_different_owners(db_path,signers):
    used="a"*64
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,grant(signers[0],nonce=used),signers)
        with pytest.raises(LedgerDenied):
            apply(db,grant(signers[0],nonce=used,owner="another"),signers)
        assert db._get("another") is None

def test_crash_safe_failed_grant_does_not_consume_nonce(db_path,signers):
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,grant(signers[0]),signers)
        nonce="b"*64
        invalid=grant(signers[0],nonce=nonce,generation=2,cp=CP2,manifest=M2)
        with pytest.raises(LedgerDenied):
            apply(db,invalid,signers)
        valid=gen2(signers,nonce=nonce)
        apply(db,valid,signers)
        assert db._get(OWNER)==(2,CP2,M2,0)

def test_same_authorized_publisher_protected_across_process_reopens(db_path,signers):
    prior=grant(signers[0])
    followup=gen2(signers)
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,prior,signers)
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,followup,signers)
    with ResearchHighWaterLedger(db_path) as db:
        with pytest.raises(LedgerDenied):
            apply(db,prior,signers)
        assert db._get(OWNER)==(2,CP2,M2,0)

def test_no_private_authorizer_key_in_source():
    from assistx import hermes_witness_grants as mod
    src=Path(mod.__file__).read_text()
    assert "Ed25519PrivateKey" not in src
    assert "Ed25519PublicKey" in src
    assert "canonical(payload)" in src


def test_atomic_competing_signed_grants_only_one_advances(db_path,signers):
    from concurrent.futures import ThreadPoolExecutor
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,grant(signers[0]),signers)
    first=gen2(signers)
    second=grant(signers[0],prior=1,prior_cp=CP1,prior_manifest=M1,
                 generation=2,cp="3"*64,manifest="c"*64)
    def attempt(raw):
        with ResearchHighWaterLedger(db_path) as db:
            try:
                apply(db,raw,signers)
                return "accepted"
            except LedgerDenied:
                return "denied"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=[x.result(timeout=5) for x in
                 (pool.submit(attempt,first),pool.submit(attempt,second))]
    assert sorted(results)==["accepted","denied"]

def test_whole_db_rollback_reopens_signed_nonce_replay_window(db_path,signers):
    # Negative limitation: without independently anchored receipts, restoring
    # the entire ledger rolls back both owner state AND consumed grant nonces.
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,grant(signers[0]),signers)
    prior=Path(db_path).read_bytes()
    g=gen2(signers)
    with ResearchHighWaterLedger(db_path) as db:
        apply(db,g,signers)
    Path(db_path).write_bytes(prior)
    with ResearchHighWaterLedger(db_path) as db:
        assert db._get(OWNER)==(1,CP1,M1,0)
        apply(db,g,signers)  # still accepted after whole-DB rollback
        assert db._get(OWNER)==(2,CP2,M2,0)
