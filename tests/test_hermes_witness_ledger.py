"""No-production, temp SQLite witness high-water proof and signature checks."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.hermes_witness_ledger import ResearchHighWaterLedger, LedgerDenied
from assistx.hermes_high_water import verify_receipt, canonical

OWNER="hermes-synthetic-owner"
CP1="1"*64
CP2="2"*64
MAN1="a"*64
MAN2="b"*64

@pytest.fixture
def place(tmp_path):
    storage=tmp_path/"separate-private-operator"
    storage.mkdir(mode=0o700)
    assert storage.stat().st_mode & 0o077==0
    return str(storage/"highwater.sqlite")


@pytest.fixture
def signer():
    key=Ed25519PrivateKey.generate()
    pub=key.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    return key,pub


def request(generation=2,checkpoint=CP2,manifest=MAN2,owner=OWNER,nonce="f"*64):
    return {"schema":1,"nonce":nonce,"owner":owner,
            "generation":generation,"checkpoint_sha256":checkpoint,
            "manifest_sha256":manifest}


def enroll(db,gen=2,cp=CP2,manifest=MAN2,owner=OWNER):
    db.publish(owner=owner,generation=gen,
               checkpoint_sha256=cp,manifest_sha256=manifest)


def signed(db,signer,req):
    raw=db.respond(raw_request=canonical(req),signer=signer[0])
    return raw


def test_approve_only_after_explicit_enrollment(place,signer):
    with ResearchHighWaterLedger(place) as ledger:
        with pytest.raises(LedgerDenied,match="unenrolled"):
            signed(ledger,signer,request())
        enroll(ledger)
        verify_receipt(signed(ledger,signer,request()),
                       public_key=signer[1],request=request())


def test_file_private_mode_and_full_durable_sqlite(place):
    with ResearchHighWaterLedger(place) as db:
        assert db.db.execute("PRAGMA synchronous").fetchone()[0]==2
        assert db.db.execute("PRAGMA journal_mode").fetchone()[0]=="delete"
        enroll(db)
    assert Path(place).stat().st_mode & 0o077==0


def test_process_restart_preserves_highwater_and_signature_acceptance(place,signer):
    with ResearchHighWaterLedger(place) as db:enroll(db)
    with ResearchHighWaterLedger(place) as db:
        verify_receipt(signed(db,signer,request()),
                       public_key=signer[1],request=request())


def test_later_generation_durable_and_old_denied_after_restart(place,signer):
    with ResearchHighWaterLedger(place) as db:enroll(db,1,CP1,MAN1)
    with ResearchHighWaterLedger(place) as db:enroll(db,2,CP2,MAN2)
    with ResearchHighWaterLedger(place) as db:
        received=signed(db,signer,request(1,CP1,MAN1))
        r=json.loads(received)
        assert r["high_water_generation"]==2
        assert r["revoked"] is True
        with pytest.raises(ValueError):
            verify_receipt(received,public_key=signer[1],
                           request=request(1,CP1,MAN1))


def test_same_generation_identical_repeat_is_idempotent(place):
    with ResearchHighWaterLedger(place) as db:
        enroll(db)
        enroll(db)
        assert db._get(OWNER)==(2,CP2,MAN2,0)


@pytest.mark.parametrize("wrong", [
    dict(gen=1,cp=CP1,manifest=MAN1),
    dict(gen=2,cp=CP1,manifest=MAN2),
    dict(gen=2,cp=CP2,manifest=MAN1),
])
def test_rollback_and_same_generation_equivocation_denied(place,wrong):
    with ResearchHighWaterLedger(place) as db:
        enroll(db)
        with pytest.raises(LedgerDenied):
            enroll(db,**wrong)
        assert db._get(OWNER)==(2,CP2,MAN2,0)


def test_monotonic_higher_generation_with_new_hashes_allowed(place):
    with ResearchHighWaterLedger(place) as db:
        enroll(db,1,CP1,MAN1)
        enroll(db,2,CP2,MAN2)
        assert db._get(OWNER)==(2,CP2,MAN2,0)


def test_revoke_persists_across_restart_and_cannot_undo(place,signer):
    with ResearchHighWaterLedger(place) as db:
        enroll(db);db.revoke(OWNER)
    with ResearchHighWaterLedger(place) as db:
        response=json.loads(signed(db,signer,request()))
        assert response["revoked"] is True
        with pytest.raises(ValueError):
            verify_receipt(canonical(response),public_key=signer[1],
                           request=request())
        with pytest.raises(LedgerDenied,match="sticky"):
            enroll(db,3,"3"*64,"c"*64)
        assert db._get(OWNER)[3]==1


def test_unknown_owner_revocation_rejected(place):
    with ResearchHighWaterLedger(place) as db:
        with pytest.raises(LedgerDenied):
            db.revoke("not-registered")


def test_owner_isolation_and_unenrolled_unknown_denied(place,signer):
    with ResearchHighWaterLedger(place) as db:
        enroll(db,owner="owner-a")
        enroll(db,4,"4"*64,"d"*64,owner="owner-b")
        assert db._get("owner-a")[0]==2
        assert db._get("owner-b")[0]==4
        with pytest.raises(LedgerDenied):
            signed(db,signer,request(owner="missing-owner"))


@pytest.mark.parametrize("bad",[
    {"owner":"owner with spaces"}, {"owner":"bad/../../file"},
    {"gen":True}, {"gen":0}, {"gen":2**63},
    {"cp":"not-hex"}, {"manifest":"0"*63}
])
def test_publisher_input_rejected_and_does_not_change_db(place,bad):
    with ResearchHighWaterLedger(place) as db:
        enroll(db)
        x=dict(owner=OWNER,gen=3,cp="3"*64,manifest="c"*64)
        x.update(bad)
        with pytest.raises(LedgerDenied):
            enroll(db,**x)
        assert db._get(OWNER)==(2,CP2,MAN2,0)


@pytest.mark.parametrize("mutation", [
    {"nonce":"wrong"}, {"nonce":None}, {"schema":True},
    {"owner":"other-owner"}, {"generation":2.5},
    {"manifest_sha256":"bad"}, {"checkpoint_sha256":"bad"}
])
def test_invalid_challenges_rejected_without_state_change(place,signer,mutation):
    with ResearchHighWaterLedger(place) as db:
        enroll(db)
        req=request();req.update(mutation)
        with pytest.raises(LedgerDenied):
            signed(db,signer,req)
        assert db._get(OWNER)==(2,CP2,MAN2,0)


def test_different_digest_at_correct_generation_returns_signed_denial(place,signer):
    with ResearchHighWaterLedger(place) as db:
        enroll(db)
        wrong=request(2,CP1,MAN2)
        response=json.loads(signed(db,signer,wrong))
        assert response["revoked"] is True
        with pytest.raises(ValueError):
            verify_receipt(canonical(response),public_key=signer[1],request=wrong)


def test_wrong_nonce_receipt_cannot_be_replayed(place,signer):
    with ResearchHighWaterLedger(place) as db:
        enroll(db)
        old=request(nonce="a"*64)
        new=request(nonce="b"*64)
        old_receipt=signed(db,signer,old)
        with pytest.raises(ValueError):
            verify_receipt(old_receipt,public_key=signer[1],request=new)


def test_ledger_read_never_promotes_generation(place,signer):
    with ResearchHighWaterLedger(place) as db:
        enroll(db)
        for _ in range(3):
            response=signed(db,signer,request(3,"3"*64,"c"*64))
            assert json.loads(response)["high_water_generation"]==2
            assert db._get(OWNER)==(2,CP2,MAN2,0)


def test_corrupt_database_fails_closed(place):
    Path(place).write_bytes(b"NOT-A-SQLITE-DATABASE")
    with pytest.raises((sqlite3.DatabaseError,LedgerDenied)):
        ResearchHighWaterLedger(place)


def test_unapproved_public_directory_denied(tmp_path):
    p=tmp_path/"world"
    p.mkdir(mode=0o777)
    p.chmod(0o755)
    with pytest.raises(LedgerDenied):
        ResearchHighWaterLedger(str(p/"data.sqlite"))


def test_ledger_rejects_symlink_path(place,tmp_path):
    path=Path(place)
    with ResearchHighWaterLedger(place) as db:enroll(db)
    alias=path.parent/"alias.sqlite"
    alias.symlink_to(path)
    with pytest.raises(LedgerDenied):
        ResearchHighWaterLedger(str(alias))


def test_no_signing_key_written_by_ledger():
    source=Path(sys.modules[ResearchHighWaterLedger.__module__].__file__).read_text()
    assert "Ed25519PrivateKey" not in source
    assert "signer.sign(canonical(response))" in source
    assert "BEGIN IMMEDIATE" in source
    assert "os.remove(" not in source


def test_two_competing_same_generation_publishers_fail_closed(place):
    from concurrent.futures import ThreadPoolExecutor
    with ResearchHighWaterLedger(place) as db:
        enroll(db,1,CP1,MAN1)
    def contender(checkpoint,manifest):
        with ResearchHighWaterLedger(place) as db:
            try:
                enroll(db,2,checkpoint,manifest)
                return "accepted"
            except LedgerDenied:
                return "denied"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(contender,CP2,MAN2),
                 pool.submit(contender,"3"*64,"c"*64)]
        results=[x.result(timeout=5) for x in futures]
    assert sorted(results)==["accepted","denied"]
    with ResearchHighWaterLedger(place) as db:
        assert db._get(OWNER)[0]==2

def test_full_sqlite_database_rollback_remains_possible_without_external_anchor(place):
    # Expected security LIMIT, not a passing production replay guarantee.
    with ResearchHighWaterLedger(place) as db:
        enroll(db,1,CP1,MAN1)
    old=Path(place).read_bytes()
    with ResearchHighWaterLedger(place) as db:
        enroll(db,2,CP2,MAN2)
    Path(place).write_bytes(old)
    with ResearchHighWaterLedger(place) as db:
        assert db._get(OWNER)==(1,CP1,MAN1,0)
