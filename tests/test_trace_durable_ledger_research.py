"""Synthetic tests: local disposable SQLite ledger; never production/API graph."""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import contextmanager
import multiprocessing
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.trace_durable_ledger_research import (
    CLOSED, RECEIPT_VERSION, Decision, DurableTraceReadLedger,
    bootstrap_disposable_fixture, _canonical,
)


@contextmanager
def fixture(slots=1):
    with tempfile.TemporaryDirectory(prefix="assistx-trace-ledger-test-", dir="/tmp") as folder:
        path=str(Path(folder)/"trace-ledger-test.sqlite")
        epoch=str(uuid.uuid4())
        signing_key=Ed25519PrivateKey.generate()
        public_key=signing_key.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        bootstrap_disposable_fixture(path,epoch,slots)
        yield DurableTraceReadLedger(path,epoch,public_key), signing_key, path, epoch, public_key


def closure(ledger, private_key, token, reference="query-one", evidence="synthetic-receipt-one"):
    body=dict(version=RECEIPT_VERSION,epoch=ledger.epoch,
              token=token,query_ref=reference,evidence_id=evidence,verdict=CLOSED)
    return body,private_key.sign(_canonical(body))


def worker_try(args):
    path,epoch,pub,index=args
    return DurableTraceReadLedger(path,epoch,pub).acquire(f"multi-worker-{index}").token


def crash_worker(args):
    path,epoch,pub=args
    ledger=DurableTraceReadLedger(path,epoch,pub)
    assert ledger.acquire("crashed-worker").token is not None
    os._exit(0)


def test_cap_never_expires_or_recovers_after_uncertain_worker_death():
    with fixture(1) as (guard,key,*_):
        first=guard.acquire("query-one")
        assert first.reason=="admitted" and first.active==1 and first.token
        assert guard.acquire("query-two")==Decision(None,1,"full")
        assert guard.acknowledge_remote_closure(
            dict(epoch=guard.epoch,token=first.token),b"") is False
        assert guard.inspect()==1
        # No TTL and no local worker exception/timeout can release it.
        time.sleep(0.01)
        assert guard.acquire("query-two").reason=="full"
        receipt,sig=closure(guard,key,first.token)
        assert guard.acknowledge_remote_closure(receipt,sig)
        assert guard.inspect()==0
        assert not guard.acknowledge_remote_closure(receipt,sig) # replay
        assert guard.acquire("query-two").token is not None


def test_signature_epoch_query_identity_and_forged_witness_refused():
    with fixture(1) as (guard,key,*_):
        token=guard.acquire("query-one").token
        good,signature=closure(guard,key,token)
        for modifier in [
            lambda p:p.update(query_ref="query-other"),
            lambda p:p.update(verdict="timeout"),
            lambda p:p.update(epoch=str(uuid.uuid4())),
            lambda p:p.update(evidence_id="../../unsafe"),
            lambda p:p.update(token="f"*32),
            lambda p:p.update(version="v0"),
            lambda p:p.update(extra="something"),
        ]:
            changed=dict(good)
            modifier(changed)
            assert not guard.acknowledge_remote_closure(changed,signature)
            assert guard.inspect()==1
        other=Ed25519PrivateKey.generate()
        assert not guard.acknowledge_remote_closure(good,other.sign(_canonical(good)))
        assert not guard.acknowledge_remote_closure(good,b"bad")
        assert guard.inspect()==1
        assert guard.acknowledge_remote_closure(good,signature)


def test_duplicate_reference_and_bad_input_fail_closed():
    with fixture(2) as (guard,key,*_):
        one=guard.acquire("query-one").token
        assert guard.acquire("query-one").reason=="duplicate-query-ref"
        for value in ["", "a"*129, "../../.env", "\u0000", None, 42, True]:
            denied=guard.acquire(value)
            assert denied.token is None
        assert guard.inspect()==1


def test_threaded_and_multi_process_slots_serialized():
    with fixture(3) as (guard,key,path,epoch,pub):
        with ThreadPoolExecutor(max_workers=12) as pool:
            result=list(pool.map(lambda n:guard.acquire(f"thread-{n}").token,range(24)))
        assert len([x for x in result if x])==3
        assert guard.inspect()==3
    with fixture(3) as (guard,key,path,epoch,pub):
        with ProcessPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(worker_try,[(path,epoch,pub,n) for n in range(18)]))
        assert len([x for x in results if x])==3
        assert len(set(x for x in results if x))==3
        assert guard.inspect()==3


def test_worker_crash_strands_physical_capacity_without_reclaim():
    with fixture(1) as (guard,key,path,epoch,pub):
        p=multiprocessing.Process(target=crash_worker,args=((path,epoch,pub),))
        p.start();p.join(10)
        assert p.exitcode==0
        # Separate process has departed without any remote termination proof.
        newer=DurableTraceReadLedger(path,epoch,pub)
        assert newer.inspect()==1
        assert newer.acquire("replacement").reason=="full"


def test_file_disappearance_cannot_silently_reinitialize_or_rearm():
    with fixture(1) as (guard,key,path,epoch,pub):
        assert guard.acquire("query-one").token
        orphan=path+".orphaned"
        os.rename(path,orphan)
        try:
            assert guard.acquire("query-two").token is None
            assert guard.inspect() is None
            assert not os.path.exists(path), "read path must not recreate missing ledger"
            with pytest.raises(Exception):
                DurableTraceReadLedger(path,epoch,pub)
        finally:
            os.rename(orphan,path)
        assert guard.acquire("query-two").reason=="full"


def test_db_identity_swap_during_same_ledger_lifetime_denied():
    with fixture(1) as (guard,key,path,epoch,pub):
        assert guard.acquire("query-one").token
        replacement=path+".replacement"
        shutil.copy2(path,replacement)
        os.replace(replacement,path)
        assert guard.acquire("query-two").token is None
        assert guard.inspect() is None


def test_epoch_mismatch_prevents_reusing_surviving_ledger():
    with fixture(1) as (guard,key,path,epoch,pub):
        assert guard.acquire("query-one").token
        with pytest.raises(ValueError, match="LEDGER_EPOCH_OR_SCHEMA_NOT_PROVEN"):
            DurableTraceReadLedger(path,str(uuid.uuid4()),pub)


def test_offline_bootstrap_cannot_rearm_existing_or_nonfixture_paths():
    with fixture(1) as (guard,key,path,epoch,pub):
        with pytest.raises(ValueError,match="RESEARCH_BOOTSTRAP_REFUSED"):
            bootstrap_disposable_fixture(path,epoch,1)
        with pytest.raises(ValueError,match="RESEARCH_BOOTSTRAP_REFUSED"):
            bootstrap_disposable_fixture("/tmp/assistx-real-ledger.sqlite",epoch,1)
        with pytest.raises(ValueError,match="RESEARCH_BOOTSTRAP_REFUSED"):
            bootstrap_disposable_fixture(path,epoch,99)


def test_split_brain_independent_copies_remain_counterexample():
    """This module is NOT a fleetwide admission solution or safe under rollback."""
    with fixture(1) as (guard,key,path,epoch,pub):
        # Offline copy of empty research DB into another disposable fixture.
        with tempfile.TemporaryDirectory(prefix="assistx-trace-ledger-test-",dir="/tmp") as folder:
            clone_path=str(Path(folder)/"trace-ledger-test.sqlite")
            shutil.copy2(path,clone_path)
            clone=DurableTraceReadLedger(clone_path,epoch,pub)
            first=guard.acquire("query-one")
            second=clone.acquire("query-two")
            assert first.token and second.token
            # Two active physical reads despite each local ledger reporting cap=1.
            assert guard.inspect()==1 and clone.inspect()==1
            assert len({first.token,second.token})==2


def test_unsigned_worker_assertion_is_never_accepted_for_release():
    with fixture(1) as (guard,key,*_):
        token=guard.acquire("query-one").token
        assert not guard.acknowledge_remote_closure({
            "version":RECEIPT_VERSION,"epoch":guard.epoch,"token":token,
            "query_ref":"query-one","evidence_id":"just-a-boolean",
            "verdict":CLOSED},b"")
        assert guard.inspect()==1


@pytest.mark.parametrize("contenders,slots,expected", [
    (1,3,1),(3,3,3),(5,3,3),(10,3,3),(10,1,1),
])
def test_preregistered_concurrent_1_3_5_10_occupancy(contenders,slots,expected):
    with fixture(slots) as (guard,key,path,epoch,pub):
        with ProcessPoolExecutor(max_workers=contenders) as pool:
            answers=list(pool.map(worker_try,[
                (path,epoch,pub,n) for n in range(contenders)]))
        assert sum(token is not None for token in answers)==expected
        assert guard.inspect()==expected


def test_corrupted_or_locked_ledger_fails_closed_not_empty():
    with fixture(1) as (guard,key,path,epoch,pub):
        assert guard.acquire("query-one").token
        # Corrupted bytes cannot be interpreted as an empty new epoch.
        conn=__import__("sqlite3").connect(path)
        conn.execute("BEGIN EXCLUSIVE")
        try:
            attempted=guard.acquire("query-two")
            assert attempted.token is None
        finally:
            conn.rollback()
            conn.close()
        assert guard.inspect()==1


def test_signed_receipt_is_not_physical_termination_proof():
    """Negative control: a dishonest verifier can sign an incorrect statement."""
    with fixture(1) as (guard,key,path,epoch,pub):
        active_token=guard.acquire("query-one").token
        # Simulated remote graph work still active. A signed text assertion cannot
        # establish that Neo4j has actually stopped running the old statement.
        physically_active={active_token}
        forged_but_validly_signed,signature=closure(guard,key,active_token)
        assert guard.acknowledge_remote_closure(forged_but_validly_signed,signature)
        successor=guard.acquire("query-two").token
        physically_active.add(successor)
        assert len(physically_active)==2 and guard.inspect()==1
        # Thus independent witness key custody and true remote observation are
        # essential release gates, not provided by this research implementation.
