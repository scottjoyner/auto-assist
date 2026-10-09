"""Synthetic receiver trust pin, local replay-recording and rollback negatives.

Research only: passing tests must NEVER be described as distributed durable
receipt custody, trusted physical termination, or authorization to release.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from copy import deepcopy
import hashlib
import multiprocessing
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from assistx.trace_receiver_evidence_research import SCHEMA, receiver_sign_only
from assistx.trace_receiver_replay_custody_research import (
    ReceiverReplayCustody, bootstrap_disposable_custody,
)
from assistx.trace_durable_ledger_research import (
    DurableTraceReadLedger, bootstrap_disposable_fixture,
)


def fixture():
    root=tempfile.TemporaryDirectory(prefix="assistx-receiver-replay-test-",dir="/tmp")
    folder=Path(root.name)
    private=Ed25519PrivateKey.generate()
    pub=private.public_key().public_bytes(
        serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    digest=hashlib.sha256(pub).hexdigest()
    epoch=str(uuid.uuid4())
    graph="a"*64
    path=str(folder/"receiver-replay-test.sqlite")
    bootstrap_disposable_custody(path,epoch,graph,digest)
    custody=ReceiverReplayCustody(path,expected_epoch=epoch,expected_graph_id=graph,
        trusted_public_key=pub,approved_signer_sha256=digest)
    observation={
        "schema":SCHEMA,"epoch":epoch,
        "token":"b"*32,"query_ref":"read-1",
        "receiver_nonce":str(uuid.uuid4()),
        "graph_container_id":graph,
        "server_transaction_id":"neo4j-transaction-17",
        "observed_running_before":True,
        "terminate_command":"TERMINATE TRANSACTIONS",
        "terminate_server_message":"Transaction terminated.",
        "server_reply_exact_id":True,
        "post_termination_same_id_visible":[False]*6,
        "observer_source":"guarded-disposable-direct-Neo4j",
        "terminal_verdict":"receiver-observed-terminated",
    }
    assert custody.register_expected(
        admission_token=observation["token"],
        query_ref=observation["query_ref"],
        receiver_nonce=observation["receiver_nonce"],
    )
    expected={
        "expected_token":observation["token"],"expected_query_ref":observation["query_ref"],
        "expected_transaction_id":observation["server_transaction_id"],
        "expected_receiver_nonce":observation["receiver_nonce"],
    }
    return root,path,custody,observation,private,pub,digest,expected


def _try_from_separate_process(args):
    path,epoch,graph,pub,digest,receipt,signature,expected=args
    custody=ReceiverReplayCustody(path,expected_epoch=epoch,expected_graph_id=graph,
        trusted_public_key=pub,approved_signer_sha256=digest)
    return custody.record(receipt,signature,**expected).accepted


def test_pretrusted_receiver_can_record_once_but_not_twice():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        signature=receiver_sign_only(receipt,private)
        first=custody.record(receipt,signature,**expected)
        assert first.accepted and first.reason=="observation-recorded-not-released"
        assert first.total_recorded==1 and custody.inspect()==1
        second=custody.record(receipt,signature,**expected)
        assert second.accepted is False
        assert second.reason=="duplicate-or-replayed-receipt"
        assert custody.inspect()==1
    finally:root.cleanup()


def test_receipt_public_key_is_not_a_trust_anchor():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        attacker=Ed25519PrivateKey.generate()
        forged=receiver_sign_only(receipt,attacker)
        assert not custody.record(receipt,forged,**expected).accepted
        fake_public=attacker.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        with pytest.raises(ValueError,match="RECEIVER_PUBLIC_KEY_NOT_APPROVED"):
            ReceiverReplayCustody(path,expected_epoch=receipt["epoch"],
                expected_graph_id=receipt["graph_container_id"],
                trusted_public_key=fake_public,approved_signer_sha256=digest)
        assert custody.inspect()==0
    finally:root.cleanup()


@pytest.mark.parametrize("case",[
    "epoch","graph","token","query","transaction","nonce","signature",
    "changed_server_response","bad_samples"
])
def test_valid_signatures_cannot_cross_expected_identity_or_invalid_truth_shape(case):
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        changed=deepcopy(receipt)
        expected=expected.copy()
        if case=="epoch":
            changed["epoch"]=str(uuid.uuid4())
        elif case=="graph":
            changed["graph_container_id"]="c"*64
        elif case=="token":
            expected["expected_token"]="c"*32
        elif case=="query":
            expected["expected_query_ref"]="different"
        elif case=="transaction":
            expected["expected_transaction_id"]="neo4j-transaction-555"
        elif case=="nonce":
            expected["expected_receiver_nonce"]=str(uuid.uuid4())
        elif case=="changed_server_response":
            changed["terminate_server_message"]="Transaction not found."
        elif case=="bad_samples":
            changed["post_termination_same_id_visible"]=[False,True,False,False]
        signature=receiver_sign_only(changed,private) if case not in (
            "changed_server_response","bad_samples") else receiver_sign_only(receipt,private)
        if case=="signature":
            signature=b"\0"*64
        assert not custody.record(changed,signature,**expected).accepted
        assert custody.inspect()==0
    finally:root.cleanup()


def test_restart_surviving_journal_prevents_replay():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        signed=receiver_sign_only(receipt,private)
        assert custody.record(receipt,signed,**expected).accepted
        reopened=ReceiverReplayCustody(path,expected_epoch=receipt["epoch"],
            expected_graph_id=receipt["graph_container_id"],
            trusted_public_key=pub,approved_signer_sha256=digest,
            external_minimum_count=1)
        assert reopened.inspect()==1
        assert reopened.record(receipt,signed,**expected).reason=="duplicate-or-replayed-receipt"
    finally:root.cleanup()


def test_concurrent_identical_receipts_record_exactly_once():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        signed=receiver_sign_only(receipt,private)
        with ThreadPoolExecutor(max_workers=10) as pool:
            results=list(pool.map(lambda _:custody.record(receipt,signed,**expected).accepted,
                                  range(10)))
        assert sum(results)==1
        assert custody.inspect()==1
    finally:root.cleanup()


def test_multiple_independent_processes_use_same_surviving_journal():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        signed=receiver_sign_only(receipt,private)
        args=(path,receipt["epoch"],receipt["graph_container_id"],
              pub,digest,receipt,signed,expected)
        with ProcessPoolExecutor(max_workers=4,
             mp_context=multiprocessing.get_context("spawn")) as pool:
            results=list(pool.map(_try_from_separate_process,[args]*4))
        assert sum(results)==1
        assert custody.inspect()==1
    finally:root.cleanup()


def test_missing_replaced_or_symlinked_journal_fails_closed(tmp_path):
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        Path(path).unlink()
        assert custody.inspect() is None
        assert not custody.record(receipt,receiver_sign_only(receipt,private),**expected).accepted
        # Refuse to silently recreate a missing journal at startup.
        with pytest.raises((OSError,sqlite3.Error,ValueError)):
            ReceiverReplayCustody(path,expected_epoch=receipt["epoch"],
                expected_graph_id=receipt["graph_container_id"],
                trusted_public_key=pub,approved_signer_sha256=digest)
    finally:root.cleanup()


def test_external_count_checkpoint_detects_rolled_back_copy():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        earlier=Path(root.name)/"snapshot.db"
        shutil.copyfile(path,earlier)
        assert custody.record(receipt,receiver_sign_only(receipt,private),**expected).accepted
        # Simulate offline filesystem rollback *without changing inode*.
        shutil.copyfile(earlier,path)
        assert custody.inspect() is None
        with pytest.raises(ValueError,match="REPLAY_JOURNAL_BELOW_PINNED_CHECKPOINT"):
            ReceiverReplayCustody(path,expected_epoch=receipt["epoch"],
                expected_graph_id=receipt["graph_container_id"],
                trusted_public_key=pub,approved_signer_sha256=digest,
                external_minimum_count=1)
    finally:root.cleanup()


def test_stale_checkpoint_after_new_process_fails_to_detect_rollback_negative_control():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        earlier=Path(root.name)/"snapshot.db"
        shutil.copyfile(path,earlier)
        signed=receiver_sign_only(receipt,private)
        assert custody.record(receipt,signed,**expected).accepted
        shutil.copyfile(earlier,path)
        # Explicit negative control: if the only claimed independent
        # authority also supplies STALE count=0 on restart, local SQLite
        # cannot detect the rollback. This MUST remain a passing counterexample.
        unsafe_new_owner=ReceiverReplayCustody(path,expected_epoch=receipt["epoch"],
            expected_graph_id=receipt["graph_container_id"],
            trusted_public_key=pub,approved_signer_sha256=digest,
            external_minimum_count=0)
        assert unsafe_new_owner.record(receipt,signed,**expected).accepted
    finally:root.cleanup()


def test_receiver_receipt_never_frees_existing_neo4j_admission_slot():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    with tempfile.TemporaryDirectory(prefix="assistx-trace-ledger-test-",dir="/tmp") as ld:
        try:
            ledgerpath=str(Path(ld)/"trace-ledger-test.sqlite")
            bootstrap_disposable_fixture(ledgerpath,receipt["epoch"],1)
            ledger=DurableTraceReadLedger(ledgerpath,receipt["epoch"],pub)
            result=ledger.acquire(receipt["query_ref"])
            assert result.token
            receipt["token"]=result.token
            receipt["receiver_nonce"]=str(uuid.uuid4())
            expected["expected_token"]=result.token
            expected["expected_receiver_nonce"]=receipt["receiver_nonce"]
            assert custody.register_expected(
                admission_token=result.token,
                query_ref=receipt["query_ref"],
                receiver_nonce=receipt["receiver_nonce"])
            signed=receiver_sign_only(receipt,private)
            assert custody.record(receipt,signed,**expected).accepted
            assert ledger.inspect()==1
            assert ledger.acquire("other-query").reason=="full"
        finally:root.cleanup()


def test_invalid_journal_location_rejected():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        with pytest.raises(ValueError,match="DISPOSABLE_RESEARCH_PATH_REQUIRED"):
            bootstrap_disposable_custody("/nas/receiver-replay-test.sqlite",
                 receipt["epoch"],receipt["graph_container_id"],digest)
    finally:root.cleanup()


@pytest.mark.parametrize("n",[1,3,5,10])
def test_same_receipt_at_1_3_5_10_clients_records_at_most_once(n):
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        sig=receiver_sign_only(receipt,private)
        # Every candidate submits the same signed identity, not a fresh token.
        with ThreadPoolExecutor(max_workers=n) as pool:
            decisions=list(pool.map(
                lambda _:custody.record(receipt,sig,**expected),range(n)))
        assert sum(d.accepted for d in decisions)==1
        assert sum(not d.accepted for d in decisions)==n-1
        assert custody.inspect()==1
    finally:root.cleanup()


def test_locked_journal_denies_without_fallback_replay():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        sig=receiver_sign_only(receipt,private)
        with sqlite3.connect(path,timeout=1,isolation_level=None) as exclusive:
            exclusive.execute("BEGIN EXCLUSIVE")
            decision=custody.record(receipt,sig,**expected)
            assert decision.accepted is False
            assert decision.reason=="custody-unavailable"
            exclusive.rollback()
        assert custody.inspect()==0
        assert custody.record(receipt,sig,**expected).accepted
    finally:root.cleanup()


def test_incorrect_trust_digest_and_epoch_cannot_reopen_journal():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        with pytest.raises(ValueError):
            ReceiverReplayCustody(path,expected_epoch=str(uuid.uuid4()),
                expected_graph_id=receipt["graph_container_id"],
                trusted_public_key=pub,approved_signer_sha256=digest)
        with pytest.raises(ValueError):
            ReceiverReplayCustody(path,expected_epoch=receipt["epoch"],
                expected_graph_id=receipt["graph_container_id"],
                trusted_public_key=pub,approved_signer_sha256="f"*64)
        with pytest.raises(ValueError,match="REPLAY_EPOCH_GRAPH_OR_SIGNER_MISMATCH"):
            ReceiverReplayCustody(path,expected_epoch=receipt["epoch"],
                expected_graph_id="c"*64,
                trusted_public_key=pub,approved_signer_sha256=digest)
    finally:root.cleanup()


def test_a_copied_local_journal_is_not_distributed_single_authority():
    # Expected counterexample: after copying the *unconsumed* snapshot to a
    # second independent local path, BOTH copies accept the same receipt.
    # Production must refuse this situation with a real shared authority.
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    with tempfile.TemporaryDirectory(prefix="assistx-receiver-replay-test-",dir="/tmp") as second:
        try:
            copy=str(Path(second)/"receiver-replay-test.sqlite")
            shutil.copyfile(path,copy)
            Path(copy).chmod(0o600)  # correct permissions still do not solve split-brain
            other=ReceiverReplayCustody(copy,expected_epoch=receipt["epoch"],
                expected_graph_id=receipt["graph_container_id"],
                trusted_public_key=pub,approved_signer_sha256=digest)
            sig=receiver_sign_only(receipt,private)
            assert custody.record(receipt,sig,**expected).accepted
            assert other.record(receipt,sig,**expected).accepted
            assert custody.inspect()==1 and other.inspect()==1
        finally:root.cleanup()


def test_valid_signature_without_preissued_request_nonce_is_denied():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        receipt["receiver_nonce"]=str(uuid.uuid4())
        expected["expected_receiver_nonce"]=receipt["receiver_nonce"]
        signed=receiver_sign_only(receipt,private)
        decision=custody.record(receipt,signed,**expected)
        assert decision.accepted is False
        assert decision.reason=="unregistered-receiver-nonce"
        assert custody.inspect()==0
    finally:root.cleanup()


def test_preissue_rejects_duplicate_token_or_nonce_and_malformed_identity():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        assert not custody.register_expected(
            admission_token=receipt["token"],query_ref=receipt["query_ref"],
            receiver_nonce=receipt["receiver_nonce"])
        assert not custody.register_expected(
            admission_token=receipt["token"],query_ref=receipt["query_ref"],
            receiver_nonce=str(uuid.uuid4()))
        assert not custody.register_expected(
            admission_token="bad-token",query_ref=receipt["query_ref"],
            receiver_nonce=str(uuid.uuid4()))
        assert not custody.register_expected(
            admission_token="c"*32,query_ref="../unsafe/path",
            receiver_nonce=str(uuid.uuid4()))
        assert custody.inspect()==0
    finally:root.cleanup()


def test_prepared_nonce_binds_token_and_query_even_with_valid_signature():
    root,path,custody,receipt,private,pub,digest,expected=fixture()
    try:
        tampered=deepcopy(receipt)
        tampered["token"]="c"*32
        args={**expected,"expected_token":tampered["token"]}
        signed=receiver_sign_only(tampered,private)
        decision=custody.record(tampered,signed,**args)
        assert not decision.accepted
        assert decision.reason=="unexpected-token-or-query"
        assert custody.inspect()==0
        # Genuine preregistered evidence still records.
        signed=receiver_sign_only(receipt,private)
        assert custody.record(receipt,signed,**expected).accepted
    finally:root.cleanup()
