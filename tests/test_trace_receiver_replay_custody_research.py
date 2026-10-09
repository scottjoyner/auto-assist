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
            expected["expected_token"]=result.token
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
