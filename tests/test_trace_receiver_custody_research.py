"""Pinned receiver trust and local replay custody, research-only regression.

All files are disposable and local. No Docker, graph, API, credentials, or
production network. Tests specifically show rollback/copy split-brain remains
unresolved, even when local replay prevention works.
"""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import contextmanager
import multiprocessing as mp
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.trace_receiver_evidence_research import SCHEMA, receiver_sign_only
from assistx.trace_receiver_custody_research import (
    LocalReceiverReceiptCustodyResearch as Custody,
    bootstrap_disposable_custody,
)


@contextmanager
def fixture():
    with tempfile.TemporaryDirectory(prefix="assistx-receipt-test-", dir="/tmp") as root:
        p=str(Path(root)/"receiver-custody.sqlite")
        key=Ed25519PrivateKey.generate()
        pub=key.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        epoch=str(uuid.uuid4())
        graph="a"*64
        bootstrap_disposable_custody(p,epoch,graph,pub)
        data={
            "schema":SCHEMA,
            "epoch":epoch, "token":"b"*32, "query_ref":"synthetic-query",
            "receiver_nonce":str(uuid.uuid4()),"graph_container_id":graph,
            "server_transaction_id":"neo4j-transaction-17",
            "observed_running_before":True,
            "terminate_command":"TERMINATE TRANSACTIONS",
            "terminate_server_message":"Transaction terminated.",
            "server_reply_exact_id":True,
            "post_termination_same_id_visible":[False]*6,
            "observer_source":"guarded-disposable-direct-Neo4j",
            "terminal_verdict":"receiver-observed-terminated",
        }
        sig=receiver_sign_only(data,key)
        expected={
            "expected_token":data["token"],
            "expected_query_ref":data["query_ref"],
            "expected_transaction_id":data["server_transaction_id"],
            "expected_receiver_nonce":data["receiver_nonce"],
        }
        yield p,epoch,graph,pub,data,sig,expected


def process_record(args):
    p,epoch,graph,pub,data,sig,expected=args
    custody=Custody(p,epoch,graph,pub)
    result=custody.record_observation_once(data,sig,**expected)
    return result.recorded,result.reason


def test_key_must_be_pinned_independently_and_not_from_receipt():
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        assert Custody(p,epoch,graph,pub).record_observation_once(
            data,sig,**expected).recorded
        key2=Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        with pytest.raises(ValueError,match="KEY_MISMATCH"):
            Custody(p,epoch,graph,key2)


def test_real_signed_observation_recorded_once_never_releases_query():
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        custody=Custody(p,epoch,graph,pub)
        first=custody.record_observation_once(data,sig,**expected)
        assert first.recorded and first.reason=="recorded_only_not_released"
        assert custody.count()==1
        # The custody module does not import or invoke physical ledger release.
        assert not hasattr(custody,"release") and not hasattr(custody,"admit")
        again=custody.record_observation_once(data,sig,**expected)
        assert not again.recorded and again.reason=="duplicate_or_replayed_receipt"
        assert custody.count()==1


def test_receipt_replay_denied_after_new_process_or_constructor():
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        assert Custody(p,epoch,graph,pub).record_observation_once(data,sig,**expected).recorded
        reopened=Custody(p,epoch,graph,pub)
        assert reopened.count()==1
        assert not reopened.record_observation_once(data,sig,**expected).recorded


@pytest.mark.parametrize("contenders",[1,3,5,10])
def test_process_concurrency_allows_only_one_record(contenders):
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        args=(p,epoch,graph,pub,data,sig,expected)
        with ProcessPoolExecutor(
            max_workers=contenders, mp_context=mp.get_context("spawn")
        ) as executor:
            outcomes=list(executor.map(process_record,[args]*contenders))
        assert sum(recorded for recorded,_ in outcomes)==1
        assert Custody(p,epoch,graph,pub).count()==1


@pytest.mark.parametrize("field,value",[
    ("expected_token","c"*32),
    ("expected_query_ref","other"),
    ("expected_transaction_id","neo4j-transaction-999"),
    ("expected_receiver_nonce",str(uuid.UUID(int=4,version=4))),
])
def test_incorrect_expected_binding_denied_before_write(field,value):
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        expected[field]=value
        result=Custody(p,epoch,graph,pub).record_observation_once(data,sig,**expected)
        assert not result.recorded
        assert result.reason=="signature_or_binding_denied"
        assert Custody(p,epoch,graph,pub).count()==0


def test_signature_cannot_be_transferred_to_other_graph_or_epoch():
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        with pytest.raises(ValueError):
            Custody(p,str(uuid.uuid4()),graph,pub)
        with pytest.raises(ValueError):
            Custody(p,epoch,"f"*64,pub)
        tampered=dict(data,graph_container_id="f"*64)
        decision=Custody(p,epoch,graph,pub).record_observation_once(
            tampered,sig,**expected)
        assert not decision.recorded


def test_explicit_invalid_signature_or_wrong_signer_denied_without_write():
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        custody=Custody(p,epoch,graph,pub)
        assert not custody.record_observation_once(data,b"0"*64,**expected).recorded
        key2=Ed25519PrivateKey.generate()
        other=receiver_sign_only(data,key2)
        assert not custody.record_observation_once(data,other,**expected).recorded
        assert custody.count()==0


def test_missing_or_replaced_ledger_does_not_reinitialize():
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        custody=Custody(p,epoch,graph,pub)
        Path(p).unlink()
        result=custody.record_observation_once(data,sig,**expected)
        assert not result.recorded and result.reason=="custody_unavailable"
        assert not Path(p).exists()
        with pytest.raises((OSError,sqlite3.Error)):
            Custody(p,epoch,graph,pub)


def test_existing_guard_refuses_in_place_file_replacement():
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        custody=Custody(p,epoch,graph,pub)
        replacement=Path(p).parent/"replacement.sqlite"
        shutil.copyfile(p,replacement)
        Path(p).unlink()
        replacement.rename(p)
        response=custody.record_observation_once(data,sig,**expected)
        assert not response.recorded and response.reason=="custody_unavailable"


def test_database_corruption_fails_closed_without_emitting_accepted_record():
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        custody=Custody(p,epoch,graph,pub)
        with sqlite3.connect(p) as conn:
            conn.execute("UPDATE custody_meta SET schema_version='wrong'")
        response=custody.record_observation_once(data,sig,**expected)
        assert not response.recorded and response.reason=="custody_unavailable"
        assert custody.count() is None


def test_disposable_bootstrap_denies_rearm_or_other_path():
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        with pytest.raises(ValueError):
            bootstrap_disposable_custody(p,epoch,graph,pub)
        with pytest.raises(ValueError):
            bootstrap_disposable_custody("/tmp/receiver-custody.sqlite",epoch,graph,pub)


def test_split_brain_copied_custody_files_still_admit_same_receipt_on_both():
    """Required negative control: local SQLite does NOT prevent global replay."""
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        other=Path(p).parent/"copy.sqlite"
        shutil.copyfile(p,other)
        first=Custody(p,epoch,graph,pub)
        second=Custody(str(other),epoch,graph,pub)
        assert first.record_observation_once(data,sig,**expected).recorded
        assert second.record_observation_once(data,sig,**expected).recorded
        assert first.count()==second.count()==1
        # A distributed authority / anti-rollback primitive is still absent.


def test_snapshot_rollback_replays_previously_consumed_nonce():
    """Required negative control: restored snapshots can erase consumed nonces."""
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        snapshot=Path(p).parent/"snapshot.sqlite"
        shutil.copyfile(p,snapshot)
        assert Custody(p,epoch,graph,pub).record_observation_once(
            data,sig,**expected).recorded
        Path(p).unlink()
        shutil.copyfile(snapshot,p)
        assert Custody(p,epoch,graph,pub).record_observation_once(
            data,sig,**expected).recorded


def test_signed_record_is_not_automatic_physical_slot_release():
    """A valid pinned receipt does not invoke the old physical-slot API."""
    from assistx.trace_durable_ledger_research import (
        DurableTraceReadLedger,bootstrap_disposable_fixture,
    )
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        ledger_root=Path(p).parent.parent/(
            "assistx-trace-ledger-test-"+uuid.uuid4().hex)
        ledger_root.mkdir(mode=0o700)
        ledger_path=str(ledger_root/"trace-ledger-test.sqlite")
        try:
            # Key deliberately unrelated to receiver's key. No path through
            # this receipt recorder can acknowledge old ledger termination.
            external=Ed25519PrivateKey.generate().public_key().public_bytes(
                serialization.Encoding.Raw,serialization.PublicFormat.Raw)
            bootstrap_disposable_fixture(ledger_path,epoch,1)
            ledger=DurableTraceReadLedger(ledger_path,epoch,external)
            assert ledger.acquire(data["query_ref"]).token
            assert ledger.inspect()==1
            custody=Custody(p,epoch,graph,pub)
            assert custody.record_observation_once(data,sig,**expected).recorded
            assert ledger.inspect()==1
            assert ledger.acquire("replacement-while-uncertain").reason=="full"
            assert not custody.record_observation_once(data,sig,**expected).recorded
            assert ledger.inspect()==1
        finally:
            shutil.rmtree(ledger_root)


def test_same_token_with_new_nonce_and_new_signature_is_not_rearmed():
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        # Re-signing a claim with a new nonce cannot reopen a consumed token.
        key=Ed25519PrivateKey.generate()
        own=key.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        # Fresh fixture pinning must happen independently from the receipt.
        new_root=Path(p).parent.parent/(
            "assistx-receipt-test-"+uuid.uuid4().hex)
        new_root.mkdir(mode=0o700)
        other_path=str(new_root/"receiver-custody.sqlite")
        try:
            bootstrap_disposable_custody(other_path,epoch,graph,own)
            custody=Custody(other_path,epoch,graph,own)
            first=receiver_sign_only(data,key)
            assert custody.record_observation_once(data,first,**expected).recorded
            changed=dict(data,receiver_nonce=str(uuid.uuid4()))
            another=receiver_sign_only(changed,key)
            second=custody.record_observation_once(
                changed,another,
                **{**expected,"expected_receiver_nonce":changed["receiver_nonce"]})
            assert not second.recorded
            assert second.reason=="duplicate_or_replayed_receipt"
            assert custody.count()==1
        finally:
            shutil.rmtree(new_root)


def test_same_server_transaction_different_token_denied_conservatively():
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        custody=Custody(p,epoch,graph,pub)
        assert custody.record_observation_once(data,sig,**expected).recorded
        # A new token may not claim the same graph/server transaction;
        # transaction ID recycling may falsely strand capacity, by design.
        key=Ed25519PrivateKey.generate()
        own=key.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        new_root=Path(p).parent.parent/(
            "assistx-receipt-test-"+uuid.uuid4().hex)
        new_root.mkdir(mode=0o700)
        path2=str(new_root/"receiver-custody.sqlite")
        try:
            bootstrap_disposable_custody(path2,epoch,graph,own)
            custody2=Custody(path2,epoch,graph,own)
            existing=receiver_sign_only(data,key)
            assert custody2.record_observation_once(data,existing,**expected).recorded
            alternative=dict(data,token="c"*32,receiver_nonce=str(uuid.uuid4()),
                             query_ref="replacement")
            result=custody2.record_observation_once(
                alternative,receiver_sign_only(alternative,key),
                expected_token=alternative["token"],
                expected_query_ref=alternative["query_ref"],
                expected_transaction_id=data["server_transaction_id"],
                expected_receiver_nonce=alternative["receiver_nonce"])
            assert not result.recorded
            assert result.reason=="duplicate_or_replayed_receipt"
        finally:
            shutil.rmtree(new_root)


def test_dishonest_but_trusted_signer_can_record_false_termination_claim():
    """Negative control: this ledger authenticates a record, not physical truth.

    A signer in possession of the independently pinned key can fabricate the
    six "not visible" flags without ever contacting Neo4j. This test must
    keep passing so the custody result cannot be mistaken for graph proof.
    """
    with fixture() as (p,epoch,graph,pub,data,sig,expected):
        trusted_signer=Ed25519PrivateKey.generate()
        key=trusted_signer.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        root=Path(p).parent.parent/(
            "assistx-receipt-test-"+uuid.uuid4().hex)
        root.mkdir(mode=0o700)
        path=str(root/"receiver-custody.sqlite")
        try:
            bootstrap_disposable_custody(path,epoch,graph,key)
            # The fixture contains invented termination claims with no graph
            # witness. Validly signing these bytes does not make them factual.
            fabricated=receiver_sign_only(data,trusted_signer)
            custody=Custody(path,epoch,graph,key)
            decision=custody.record_observation_once(data,fabricated,**expected)
            assert decision.recorded
            assert custody.count()==1
            # This must NEVER be translated into automatic admission release.
            assert not hasattr(custody,"release")
        finally:
            shutil.rmtree(root)
