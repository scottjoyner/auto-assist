"""Research-only xwing witness negatives and no-fallback primary orchestration."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import shutil
import tempfile
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from assistx.trace_two_host_witness_research import (
    Witness, bootstrap_disposable_witness, verify_external_grant,
)


GRAPH="a"*64


def fixture():
    epoch=str(uuid.uuid4())
    folder="/tmp/assistx-twohost-witness-test-"+uuid.uuid4().hex
    pub=bytes.fromhex(bootstrap_disposable_witness(folder,epoch=epoch,graph_id=GRAPH))
    w=Witness(folder,epoch=epoch,graph_id=GRAPH,expected_public_key=pub)
    return folder,epoch,pub,w


@pytest.mark.parametrize("n",[1,3,5,10])
def test_single_surviving_witness_allows_only_one_of_n(n):
    folder,epoch,pub,w=fixture()
    try:
        with ThreadPoolExecutor(max_workers=n) as pool:
            results=list(pool.map(lambda i:w.reserve(f"synthetic-worker-{i}"),range(n)))
        assert sum(r["status"]=="reserved" for r in results)==1
        assert sum(r["status"]=="full" for r in results)==n-1
        approved=next(r for r in results if r["status"]=="reserved")
        grant=approved["grant"]
        assert verify_external_grant(approved,pub,epoch=epoch,graph_id=GRAPH,
                                     query_ref=grant["query_ref"])
        assert w.snapshot()["active"]==1
    finally:shutil.rmtree(folder)


def test_wrong_signing_key_or_reply_identity_cannot_authorize_primary():
    folder,epoch,pub,w=fixture()
    try:
        result=w.reserve("synthetic-real")
        fake_pub=Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        assert not verify_external_grant(result,fake_pub,epoch=epoch,
                                         graph_id=GRAPH,query_ref="synthetic-real")
        assert not verify_external_grant(result,pub,epoch=epoch,
                                         graph_id=GRAPH,query_ref="synthetic-other")
        assert not verify_external_grant(result,pub,epoch=str(uuid.uuid4()),
                                         graph_id=GRAPH,query_ref="synthetic-real")
        forged=dict(result)
        forged["signature_hex"]="0"*128
        assert not verify_external_grant(forged,pub,epoch=epoch,
                                         graph_id=GRAPH,query_ref="synthetic-real")
    finally:shutil.rmtree(folder)


def test_existing_witness_is_not_rebootstrapped_or_expired():
    folder,epoch,pub,w=fixture()
    try:
        assert w.reserve("synthetic-first")["status"]=="reserved"
        assert Witness(folder,epoch=epoch,graph_id=GRAPH,
                       expected_public_key=pub).reserve("synthetic-second")["status"]=="full"
        with pytest.raises(ValueError,match="RESEARCH_WITNESS_EXISTS_NO_REARM"):
            bootstrap_disposable_witness(folder,epoch=epoch,graph_id=GRAPH)
    finally:shutil.rmtree(folder)


def test_missing_untrusted_and_replaced_files_fail_closed():
    folder,epoch,pub,w=fixture()
    try:
        different=Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        with pytest.raises(ValueError):
            Witness(folder,epoch=epoch,graph_id=GRAPH,expected_public_key=different)
        Path(folder,"witness-test.key").unlink()
        assert w.reserve("synthetic-missing")["status"]=="witness-unavailable"
        with pytest.raises((OSError,ValueError)):
            Witness(folder,epoch=epoch,graph_id=GRAPH,expected_public_key=pub)
    finally:shutil.rmtree(folder)


def test_rolled_back_witness_blocks_only_when_pinned_sequence_survives():
    folder,epoch,pub,w=fixture()
    try:
        db=Path(folder,"witness-test.sqlite")
        snapshot=Path(folder,"before-first.snapshot")
        shutil.copyfile(db,snapshot)
        original=w.reserve("synthetic-first")
        assert original["status"]=="reserved"
        shutil.copyfile(snapshot,db) # same inode, restored older bytes
        assert w.reserve("synthetic-after-rollback")["status"]=="witness-unavailable"
        with pytest.raises(ValueError,match="WITNESS_EPOCH_OR_ROLLBACK_UNTRUSTED"):
            Witness(folder,epoch=epoch,graph_id=GRAPH,
                expected_public_key=pub,minimum_sequence=1)
        # DELIBERATE COUNTEREXAMPLE: a restarted witness with stale
        # checkpoint zero reissues capacity after snapshot rollback.
        unsafe=Witness(folder,epoch=epoch,graph_id=GRAPH,
                       expected_public_key=pub,minimum_sequence=0)
        assert unsafe.reserve("synthetic-unsafe-rearm")["status"]=="reserved"
    finally:shutil.rmtree(folder)


def test_two_copied_valid_witnesses_both_reserve_one_negative_control():
    folder,epoch,pub,w=fixture()
    other="/tmp/assistx-twohost-witness-test-"+uuid.uuid4().hex
    try:
        shutil.copytree(folder,other)
        w2=Witness(other,epoch=epoch,graph_id=GRAPH,expected_public_key=pub)
        a=w.reserve("synthetic-one")
        b=w2.reserve("synthetic-two")
        assert a["status"]=="reserved" and b["status"]=="reserved"
        assert a["grant"]["sequence"]==b["grant"]["sequence"]==1
        assert a["grant"]["reservation_token"]!=b["grant"]["reservation_token"]
    finally:
        shutil.rmtree(folder)
        shutil.rmtree(other)


def test_invalid_fixture_location_refused():
    with pytest.raises(ValueError,match="DISPOSABLE_WITNESS_PATH_ONLY"):
        bootstrap_disposable_witness("/nas/actual",epoch=str(uuid.uuid4()),graph_id=GRAPH)


def test_wrong_graph_epoch_and_sequence_denied():
    folder,epoch,pub,w=fixture()
    try:
        with pytest.raises(ValueError):
            Witness(folder,epoch=str(uuid.uuid4()),graph_id=GRAPH,expected_public_key=pub)
        with pytest.raises(ValueError):
            Witness(folder,epoch=epoch,graph_id="b"*64,expected_public_key=pub)
        with pytest.raises(ValueError):
            Witness(folder,epoch=epoch,graph_id=GRAPH,expected_public_key=pub,
                    minimum_sequence=1)
    finally:shutil.rmtree(folder)


def test_transport_failure_has_no_local_primary_fallback(monkeypatch):
    from probe_trace_witness_fence_cli import two_step_admit
    import probe_trace_witness_fence_cli as cli
    monkeypatch.setenv("ASSISTX_TWOHOST_RESEARCH_ONLY","yes-disposable")
    monkeypatch.setattr(cli.socket,"gethostname",lambda:"x1-370")
    monkeypatch.setattr(cli,"_invoke_witness",lambda *a,**k:{"status":"witness-unavailable"})
    class NeverPrimary:
        def __init__(self,*a,**kw):
            raise AssertionError("PRIMARY_MUST_NOT_RUN")
    monkeypatch.setattr(cli,"SingleAuthorityResearch",NeverPrimary)
    response=two_step_admit(
        primary_db="/tmp/assistx-twohost-authority-test-synthetic/authority-test.sqlite",
        witness_dir="/tmp/assistx-twohost-witness-test-synthetic",
        epoch=str(uuid.uuid4()),pinned_public=b"x"*32,query_ref="synthetic-first")
    assert response=={"status":"witness-unavailable","primary_called":False}


def test_untrusted_witness_reply_does_not_touch_primary(monkeypatch):
    from probe_trace_witness_fence_cli import _invoke_witness
    import probe_trace_witness_fence_cli as cli
    monkeypatch.setattr(cli.subprocess,"run",
        lambda *a,**kw:type("R",(),{
            "returncode":0,"stdout":'{"status":"reserved","grant":{},"signature_hex":"00"}'})())
    row=_invoke_witness("/tmp/assistx-twohost-witness-test-guard",
                        epoch=str(uuid.uuid4()),query_ref="synthetic-first",
                        pinned_pub=b"z"*32)
    assert row["status"]=="untrusted-witness-response"


def test_signed_witness_grant_is_exact_primary_token_nonce_and_sequence():
    from assistx.trace_two_host_authority_research import (
        bootstrap_research,SingleAuthorityResearch,
    )
    from assistx.trace_two_host_witness_research import apply_witness_grant_to_primary
    folder,epoch,pub,w=fixture()
    with tempfile.TemporaryDirectory(
        prefix="assistx-twohost-authority-test-",dir="/tmp") as owner_dir:
        try:
            db=str(Path(owner_dir)/"authority-test.sqlite")
            bootstrap_research(db,epoch=epoch,graph_id=GRAPH,capacity=1)
            primary=SingleAuthorityResearch(
                db,pinned_epoch=epoch,pinned_graph_id=GRAPH,minimum_sequence=0)
            row=w.reserve("synthetic-first")
            result=apply_witness_grant_to_primary(primary,row,pub,query_ref="synthetic-first")
            assert result["status"]=="admitted"
            assert result["reservation_token"]==row["grant"]["reservation_token"]
            assert result["receiver_nonce"]==row["grant"]["receiver_nonce"]
            assert result["primary_sequence"]==row["grant"]["sequence"]==1
            assert primary.snapshot()["active"]==1
            assert apply_witness_grant_to_primary(
                primary,row,pub,query_ref="synthetic-first")["status"]=="full"
        finally:shutil.rmtree(folder)


def test_untrusted_witness_does_not_touch_primary():
    from assistx.trace_two_host_authority_research import (
        bootstrap_research,SingleAuthorityResearch,
    )
    from assistx.trace_two_host_witness_research import apply_witness_grant_to_primary
    folder,epoch,pub,w=fixture()
    with tempfile.TemporaryDirectory(
        prefix="assistx-twohost-authority-test-",dir="/tmp") as owner_dir:
        try:
            db=str(Path(owner_dir)/"authority-test.sqlite")
            bootstrap_research(db,epoch=epoch,graph_id=GRAPH)
            primary=SingleAuthorityResearch(db,pinned_epoch=epoch,
                                             pinned_graph_id=GRAPH,minimum_sequence=0)
            row=w.reserve("synthetic-first")
            forged=dict(row)
            forged["signature_hex"]="00"*64
            denied=apply_witness_grant_to_primary(
                primary,forged,pub,query_ref="synthetic-first")
            assert denied["status"]=="untrusted-witness-grant"
            assert primary.snapshot()["active"]==0
        finally:shutil.rmtree(folder)


def test_replay_of_old_signed_grant_after_owner_rollback_still_admits_negative_control():
    from assistx.trace_two_host_authority_research import (
        bootstrap_research,SingleAuthorityResearch,
    )
    from assistx.trace_two_host_witness_research import apply_witness_grant_to_primary
    folder,epoch,pub,w=fixture()
    with tempfile.TemporaryDirectory(
        prefix="assistx-twohost-authority-test-",dir="/tmp") as owner_dir:
        try:
            db=str(Path(owner_dir)/"authority-test.sqlite")
            bootstrap_research(db,epoch=epoch,graph_id=GRAPH)
            snapshot=Path(owner_dir)/"empty-snapshot"
            shutil.copyfile(db,snapshot)
            row=w.reserve("synthetic-first")
            before=SingleAuthorityResearch(db,pinned_epoch=epoch,
                                            pinned_graph_id=GRAPH,minimum_sequence=0)
            assert apply_witness_grant_to_primary(
                before,row,pub,query_ref="synthetic-first")["status"]=="admitted"
            assert w.snapshot()["active"]==1
            shutil.copyfile(snapshot,db)
            # With a current external high-water checkpoint, refusal holds.
            with pytest.raises(ValueError):
                SingleAuthorityResearch(db,pinned_epoch=epoch,
                                         pinned_graph_id=GRAPH,minimum_sequence=1)
            # Deliberate vulnerability counterexample: stale x1 checkpoint
            # and replayed offline signed grant can rearm primary even while
            # xwing's witness still remembers it is occupied.
            stale=SingleAuthorityResearch(db,pinned_epoch=epoch,
                                          pinned_graph_id=GRAPH,minimum_sequence=0)
            assert apply_witness_grant_to_primary(
                stale,row,pub,query_ref="synthetic-first")["status"]=="admitted"
            assert w.snapshot()["active"]==1
        finally:shutil.rmtree(folder)


def test_witness_committed_but_primary_unavailable_strands_capacity():
    folder,epoch,pub,w=fixture()
    try:
        row=w.reserve("synthetic-first")
        assert row["status"]=="reserved"
        assert w.snapshot()["active"]==1
        # A crashed primary cannot force the witness to forget this slot.
        assert w.reserve("synthetic-after-primary-crash")["status"]=="full"
    finally:shutil.rmtree(folder)


def test_legacy_research_owner_can_still_bypass_witness_negative_control():
    # This is a deliberate counterexample, NOT safe by design: importing the
    # old directly callable single-authority research method does not require
    # the new witness. A real admission API must remove this bypass and
    # enforce signed online witness approval at its ONLY entry point.
    from assistx.trace_two_host_authority_research import (
        SingleAuthorityResearch, bootstrap_research
    )
    with tempfile.TemporaryDirectory(
        prefix="assistx-twohost-authority-test-",dir="/tmp") as owner_dir:
        path=str(Path(owner_dir)/"authority-test.sqlite")
        epoch=str(uuid.uuid4())
        bootstrap_research(path,epoch=epoch,graph_id=GRAPH,capacity=1)
        primary=SingleAuthorityResearch(path,pinned_epoch=epoch,
                                        pinned_graph_id=GRAPH,minimum_sequence=0)
        assert primary.admit("synthetic-unwitnessed-bypass").status=="admitted"
        assert primary.snapshot()["active"]==1
