"""Research: pinned receiver identity, atomic local replay denial, rollback negatives.

All files are disposable /tmp fixtures. These checks NEVER release an
admission slot or establish a fleetwide distributed fencing authority.
"""
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.trace_receiver_custody_research import (
    ReceiverReceiptCustody, PinnedCheckpoint,
    bootstrap_disposable_receiver_custody,
)
from assistx.trace_receiver_evidence_research import (
    SCHEMA, receiver_sign_only,
)


def _public(private):
    return private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _receipt(epoch, *, token=None, nonce=None, graph=None, tx="neo4j-transaction-17"):
    evidence={
        "schema":SCHEMA, "epoch":epoch,
        "token":token or uuid.uuid4().hex,
        "query_ref":"synthetic-read",
        "receiver_nonce":nonce or str(uuid.uuid4()),
        "graph_container_id":graph or "b"*64,
        "server_transaction_id":tx,
        "observed_running_before":True,
        "terminate_command":"TERMINATE TRANSACTIONS",
        "terminate_server_message":"Transaction terminated.",
        "server_reply_exact_id":True,
        "post_termination_same_id_visible":[False]*6,
        "observer_source":"guarded-disposable-direct-Neo4j",
        "terminal_verdict":"receiver-observed-terminated",
    }
    return evidence


def _args(e):
    return dict(
        expected_token=e["token"], expected_query_ref=e["query_ref"],
        expected_graph_container_id=e["graph_container_id"],
        expected_transaction_id=e["server_transaction_id"],
        expected_receiver_nonce=e["receiver_nonce"],
    )


@pytest.fixture()
def isolated():
    private=Ed25519PrivateKey.generate()
    pub=_public(private)
    epoch=str(uuid.uuid4())
    with tempfile.TemporaryDirectory(prefix="assistx-trace-custody-test-",dir="/tmp") as root:
        p=str(Path(root)/"receiver-custody-test.sqlite")
        genesis=bootstrap_disposable_receiver_custody(p,epoch,pub)
        yield p,epoch,private,pub,genesis


def _open(f):
    p,epoch,private,pub,cp=f
    return ReceiverReceiptCustody(p,expected_epoch=epoch,
                                  operator_pinned_public_key=pub,
                                  trusted_checkpoint=cp)


def test_operator_pinned_key_records_once_and_does_not_release(isolated):
    ledger=_open(isolated)
    p,epoch,key,pub,genesis=isolated
    e=_receipt(epoch)
    sig=receiver_sign_only(e,key)
    result=ledger.observe_once(e,sig,**_args(e))
    assert result.accepted and result.reason=="recorded_no_release"
    assert result.checkpoint.sequence==1
    assert ledger.inspect()==result.checkpoint
    assert ledger.observe_once(e,sig,**_args(e)).accepted is False
    assert ledger.inspect().sequence==1
    assert not hasattr(ledger,"acknowledge_remote_closure")
    assert not hasattr(ledger,"release")
    assert not hasattr(ledger,"acquire")


@pytest.mark.parametrize("wrong",[
    "key", "signature", "nonce", "transaction", "graph", "token",
    "epoch", "payload_tamper",
])
def test_pin_or_payload_mismatch_denied_before_storage(isolated,wrong):
    ledger=_open(isolated)
    p,epoch,key,pub,genesis=isolated
    e=_receipt(epoch)
    sig=receiver_sign_only(e,key)
    args=_args(e)
    if wrong=="key":
        fake=Ed25519PrivateKey.generate()
        sig=receiver_sign_only(e,fake)
    if wrong=="signature":sig=b"x"*64
    if wrong=="nonce":args["expected_receiver_nonce"]=str(uuid.uuid4())
    if wrong=="transaction":args["expected_transaction_id"]="neo4j-transaction-32"
    if wrong=="graph":args["expected_graph_container_id"]="c"*64
    if wrong=="token":args["expected_token"]="d"*32
    if wrong=="epoch":e["epoch"]=str(uuid.uuid4())
    if wrong=="payload_tamper":e["post_termination_same_id_visible"][0]=True
    result=ledger.observe_once(e,sig,**args)
    assert result.accepted is False
    assert result.reason=="invalid_or_untrusted_receipt"
    assert ledger.inspect()==genesis


def test_wrong_key_constructor_cannot_be_authorized_by_receipt_key(isolated):
    p,epoch,key,pub,cp=isolated
    impostor=Ed25519PrivateKey.generate()
    with pytest.raises(ValueError,match="AUTHORITY_CHANGED"):
        ReceiverReceiptCustody(p,expected_epoch=epoch,
            operator_pinned_public_key=_public(impostor),trusted_checkpoint=cp)


def test_reopen_requires_exact_external_checkpoint(isolated):
    custody=_open(isolated)
    p,epoch,key,pub,initial=isolated
    e=_receipt(epoch)
    decision=custody.observe_once(e,receiver_sign_only(e,key),**_args(e))
    assert decision.accepted
    with pytest.raises(ValueError,match="CHECKPOINT_MISMATCH"):
        _open(isolated)  # externally pinned GENESIS is now stale
    reopened=_open((p,epoch,key,pub,decision.checkpoint))
    assert reopened.inspect()==decision.checkpoint
    assert not reopened.observe_once(e,receiver_sign_only(e,key),**_args(e)).accepted


def test_external_checkpoint_rejects_replaced_rollback_file(isolated):
    p,epoch,key,pub,initial=isolated
    rollback=Path(p).with_suffix(".old")
    shutil.copy2(p,rollback)
    custody=_open(isolated)
    e=_receipt(epoch)
    after=custody.observe_once(e,receiver_sign_only(e,key),**_args(e))
    assert after.accepted
    # Simulate local disk snapshot rollback. Caller retains independently
    # pinned latest checkpoint. This does NOT model *both* reverting.
    shutil.copy2(rollback,p)
    with pytest.raises(ValueError,match="CHECKPOINT_MISMATCH"):
        _open((p,epoch,key,pub,after.checkpoint))
    assert custody.inspect() is None


def test_missing_file_never_reinitialized(isolated):
    p,epoch,key,pub,cp=isolated
    custody=_open(isolated)
    Path(p).unlink()
    e=_receipt(epoch)
    assert custody.observe_once(e,receiver_sign_only(e,key),**_args(e)).reason=="unavailable"
    assert not Path(p).exists()
    with pytest.raises((OSError,sqlite3.Error)):
        _open(isolated)


def test_wrong_epoch_cannot_rearm_occupied_custody(isolated):
    p,epoch,key,pub,cp=isolated
    with pytest.raises(ValueError,match="AUTHORITY_CHANGED"):
        ReceiverReceiptCustody(p,expected_epoch=str(uuid.uuid4()),
          operator_pinned_public_key=pub,trusted_checkpoint=cp)


def test_stale_checkpoint_fails_closed_for_other_process_state(isolated):
    p,epoch,key,pub,cp=isolated
    first=_open(isolated)
    stale=_open(isolated)
    e1=_receipt(epoch,tx="neo4j-transaction-17")
    result=first.observe_once(e1,receiver_sign_only(e1,key),**_args(e1))
    assert result.accepted
    e2=_receipt(epoch,tx="neo4j-transaction-18")
    denied=stale.observe_once(e2,receiver_sign_only(e2,key),**_args(e2))
    assert denied.reason=="checkpoint_mismatch"
    assert stale.inspect() is None
    next_writer=_open((p,epoch,key,pub,result.checkpoint))
    assert next_writer.observe_once(e2,receiver_sign_only(e2,key),**_args(e2)).accepted


def test_graph_and_token_uniqueness_across_distinct_nonces(isolated):
    p,epoch,key,pub,cp=isolated
    custody=_open(isolated)
    e=_receipt(epoch)
    assert custody.observe_once(e,receiver_sign_only(e,key),**_args(e)).accepted
    # Distinct nonce/signature but same graph transaction is a collision.
    repeated=_receipt(epoch,token=uuid.uuid4().hex,nonce=str(uuid.uuid4()))
    assert custody.observe_once(repeated,receiver_sign_only(repeated,key),**_args(repeated)).reason=="already_consumed_or_collision"
    # Same token with a different graph transaction is also denied.
    another=_receipt(epoch,token=e["token"],tx="neo4j-transaction-19")
    assert custody.observe_once(another,receiver_sign_only(another,key),**_args(another)).reason=="already_consumed_or_collision"


def test_disposable_bootstrap_rejects_non_tmp_paths_and_existing(isolated):
    p,epoch,key,pub,cp=isolated
    with pytest.raises(ValueError,match="BOOTSTRAP_REFUSED"):
        bootstrap_disposable_receiver_custody(p,epoch,pub)
    with pytest.raises(ValueError,match="BOOTSTRAP_REFUSED"):
        bootstrap_disposable_receiver_custody("/nas/receiver-custody-test.sqlite",epoch,pub)


def _independent_process_attempt(payload):
    p,epoch,pub,checkpoint,evidence,signature=payload
    try:
        ledger=ReceiverReceiptCustody(
            p,expected_epoch=epoch,operator_pinned_public_key=pub,
            trusted_checkpoint=PinnedCheckpoint(*checkpoint))
        decision=ledger.observe_once(evidence,signature,**_args(evidence))
        return decision.reason
    except Exception:
        return "startup_fail_closed"


def test_spawned_processes_never_double_consume_same_receipt(isolated):
    p,epoch,key,pub,cp=isolated
    e=_receipt(epoch)
    signature=receiver_sign_only(e,key)
    payload=(p,epoch,pub,(cp.sequence,cp.head_sha256),e,signature)
    with ProcessPoolExecutor(max_workers=5,
          mp_context=mp.get_context("spawn")) as pool:
        outcomes=list(pool.map(_independent_process_attempt,[payload]*5))
    assert outcomes.count("recorded_no_release")==1, outcomes
    assert all(x in ("recorded_no_release","checkpoint_mismatch",
                     "startup_fail_closed","already_consumed_or_collision") for x in outcomes)
    # The stale pinned genesis can no longer reopen after a successful write.
    with pytest.raises(ValueError,match="CHECKPOINT_MISMATCH"):
        _open(isolated)


def test_copied_store_split_brain_is_explicit_negative_control(isolated):
    """Identical independent local stores ACCEPT twice: distributed NO-GO."""
    p,epoch,key,pub,cp=isolated
    with tempfile.TemporaryDirectory(prefix="assistx-trace-custody-test-",dir="/tmp") as other:
        copied=str(Path(other)/"receiver-custody-test.sqlite")
        shutil.copy2(p,copied)
        a=_open(isolated)
        b=_open((copied,epoch,key,pub,cp))
        e=_receipt(epoch)
        sig=receiver_sign_only(e,key)
        assert a.observe_once(e,sig,**_args(e)).accepted
        assert b.observe_once(e,sig,**_args(e)).accepted
        assert a.inspect().sequence==1
        assert b.inspect().sequence==1
        # Local unique constraints do not enforce a global uniqueness gate.


def test_external_checkpoint_lost_after_commit_must_hold_closed(isolated):
    """Committed but unacknowledged checkpoint strands safe progress."""
    custody=_open(isolated)
    p,epoch,key,pub,genesis=isolated
    e=_receipt(epoch)
    assert custody.observe_once(e,receiver_sign_only(e,key),**_args(e)).accepted
    # Simulate loss of the update sent to independent checkpoint custody:
    with pytest.raises(ValueError,match="CHECKPOINT_MISMATCH"):
        _open((p,epoch,key,pub,genesis))


def test_unsafe_file_permissions_block_new_authority(isolated):
    p,epoch,key,pub,cp=isolated
    Path(p).chmod(0o644)
    with pytest.raises(ValueError,match="UNSAFE_CUSTODY_FILE"):
        _open(isolated)


def test_damaged_progress_denied_without_fallback(isolated):
    p,epoch,key,pub,cp=isolated
    custody=_open(isolated)
    with sqlite3.connect(p) as connection:
        connection.execute("UPDATE progress SET head_sha256=? WHERE id=1",("0"*64,))
    e=_receipt(epoch)
    assert custody.inspect() is None
    assert custody.observe_once(e,receiver_sign_only(e,key),**_args(e)).reason=="unavailable"
    with pytest.raises(ValueError,match="HASHCHAIN_DIVERGED"):
        _open(isolated)


def test_dropped_table_denies_read_and_consumption(isolated):
    p,epoch,key,pub,cp=isolated
    custody=_open(isolated)
    with sqlite3.connect(p) as connection:
        connection.execute("DROP TABLE received")
    e=_receipt(epoch)
    assert custody.inspect() is None
    assert custody.observe_once(e,receiver_sign_only(e,key),**_args(e)).reason=="unavailable"


def test_signature_is_not_physical_truth_even_with_pinned_key(isolated):
    """A deliberately dishonest trusted signer can sign false observation data.

    Validation cannot replace an external physical observer or real key custody.
    """
    p,epoch,key,pub,cp=isolated
    custody=_open(isolated)
    forged=_receipt(epoch)
    signature=receiver_sign_only(forged,key)
    # NO Neo4j container or server transaction has been contacted here.
    result=custody.observe_once(forged,signature,**_args(forged))
    assert result.accepted
    assert result.reason=="recorded_no_release"
    assert custody.inspect().sequence==1
    assert not hasattr(custody,"release")


def test_key_pinning_does_not_come_from_envelope(isolated):
    p,epoch,key,pub,cp=isolated
    assert "public_key" not in _receipt(epoch)
    with pytest.raises(ValueError,match="PUBLIC_KEY_REQUIRED"):
        ReceiverReceiptCustody(p,expected_epoch=epoch,
            operator_pinned_public_key=b"",trusted_checkpoint=cp)


def test_receipt_row_tampering_is_detected_by_full_commitment_chain(isolated):
    p,epoch,key,pub,cp=isolated
    ledger=_open(isolated)
    receipt=_receipt(epoch)
    result=ledger.observe_once(receipt,receiver_sign_only(receipt,key),**_args(receipt))
    assert result.accepted
    with sqlite3.connect(p) as db:
        db.execute("UPDATE received SET receipt_sha256=?",( "f"*64,))
    assert ledger.inspect() is None
    next_receipt=_receipt(epoch,tx="neo4j-transaction-18")
    assert ledger.observe_once(next_receipt,receiver_sign_only(next_receipt,key),
                               **_args(next_receipt)).reason=="unavailable"
    with pytest.raises(ValueError,match="HASHCHAIN_DIVERGED"):
        _open((p,epoch,key,pub,result.checkpoint))


def test_non_contiguous_sequence_detected_before_receipt_acceptance(isolated):
    p,epoch,key,pub,cp=isolated
    ledger=_open(isolated)
    receipt=_receipt(epoch)
    result=ledger.observe_once(receipt,receiver_sign_only(receipt,key),**_args(receipt))
    assert result.accepted
    with sqlite3.connect(p) as db:
        db.execute("UPDATE received SET sequence=3")
    assert ledger.inspect() is None
    with pytest.raises(ValueError,match="RECEIPT_SEQUENCE_INVALID"):
        _open((p,epoch,key,pub,result.checkpoint))


def test_research_custody_refuses_arbitrary_existing_file_location(isolated,tmp_path):
    p,epoch,key,pub,cp=isolated
    arbitrary=tmp_path/"receiver-custody-test.sqlite"
    shutil.copy2(p,arbitrary)
    with pytest.raises(ValueError,match="ONLY_DISPOSABLE_RESEARCH_CUSTODY_PATH_ALLOWED"):
        ReceiverReceiptCustody(str(arbitrary),expected_epoch=epoch,
            operator_pinned_public_key=pub,trusted_checkpoint=cp)


def test_research_custody_rejects_nonprivate_directory(isolated):
    p,epoch,key,pub,cp=isolated
    directory=Path(p).parent
    directory.chmod(0o755)
    try:
        with pytest.raises(ValueError,match="UNSAFE_CUSTODY_DIRECTORY"):
            _open(isolated)
    finally:
        directory.chmod(0o700)


def test_bootstrap_rejects_unsafe_disposable_parent_mode(isolated):
    p,epoch,key,pub,cp=isolated
    with tempfile.TemporaryDirectory(prefix="assistx-trace-custody-test-",dir="/tmp") as root:
        directory=Path(root)
        directory.chmod(0o777)
        try:
            with pytest.raises(ValueError,match="INVALID_CUSTODY_DIRECTORY"):
                bootstrap_disposable_receiver_custody(
                    str(directory/"receiver-custody-test.sqlite"),epoch,pub)
        finally:
            directory.chmod(0o700)
