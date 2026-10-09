"""No network: secondary veto witness accepts one prepared research slot only.

Includes two explicit expected counterexamples. A private signer key copied
alongside its witness journal permits an independent fork. No production
integration, execution grant, automatic release or failover promotion.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import os
import shutil
import tempfile
import uuid

import pytest
from assistx.trace_two_host_veto_witness_research import (
    VetoWitnessResearch,bootstrap_disposable_witness,
    verify_pretrusted_endorsement,
)

GRAPH="a"*64


def fixture():
    folder=tempfile.TemporaryDirectory(prefix="assistx-twohost-witness-test-",dir="/tmp")
    # Bootstrap requires a NONEXISTENT directory; reserve its parent with
    # tempfile but create the witness in a fresh sibling matching the guard.
    folder.cleanup()
    epoch=str(uuid.uuid4())
    enrolled=bootstrap_disposable_witness(folder.name,epoch,GRAPH)
    witness=VetoWitnessResearch(folder.name,pinned_epoch=epoch,pinned_graph_id=GRAPH,
        pinned_signer_digest=enrolled["signer_sha256"])
    args={
        "token":"b"*32,"receiver_nonce":str(uuid.uuid4()),
        "query_ref":"synthetic-request-1","sequence":1,
    }
    return folder.name,epoch,enrolled,witness,args


def cleanup(folder):
    root=Path(folder)
    if root.exists():
        for f in root.iterdir():
            assert f.name in {"witness-test.key","witness-test.pub","witness-test.sqlite"}
            assert f.is_file() and not f.is_symlink()
            f.unlink()
        root.rmdir()


def test_preissued_identity_bound_signature_is_not_execution_grant():
    folder,epoch,enrolled,witness,args=fixture()
    try:
        answer=witness.endorse(**args)
        assert answer["status"]=="witnessed-not-executable"
        assert "public_key_hex" not in answer
        assert verify_pretrusted_endorsement(
            answer,public_key=enrolled["public_key"],
            approved_sha256=enrolled["signer_sha256"],
            epoch=epoch,graph_id=GRAPH,**args)
        assert witness.inspect()["occupied"]==1
        assert witness.endorse(**args)["status"]=="stale-or-out-of-order"
    finally:cleanup(folder)


@pytest.mark.parametrize("mutated",["epoch","graph","token","nonce","query_ref","sequence","signer"])
def test_mismatched_pinned_identity_rejects_endorsement(mutated):
    folder,epoch,enrolled,witness,args=fixture()
    try:
        reply=witness.endorse(**args)
        expected={
            "public_key":enrolled["public_key"],
            "approved_sha256":enrolled["signer_sha256"],
            "epoch":epoch,"graph_id":GRAPH,**args,
        }
        if mutated=="epoch":expected["epoch"]=str(uuid.uuid4())
        if mutated=="graph":expected["graph_id"]="c"*64
        if mutated=="token":expected["token"]="d"*32
        if mutated=="nonce":expected["receiver_nonce"]=str(uuid.uuid4())
        if mutated=="query_ref":expected["query_ref"]="unregistered-query"
        if mutated=="sequence":expected["sequence"]=2
        if mutated=="signer":expected["approved_sha256"]="f"*64
        assert not verify_pretrusted_endorsement(reply,**expected)
    finally:cleanup(folder)


@pytest.mark.parametrize("n",[1,3,5,10])
def test_competing_prepared_proposals_get_at_most_one_witness(n):
    folder,epoch,enrolled,witness,args=fixture()
    try:
        def attempt(i):
            proposed={**args,"token":f"{i:032x}",
                      "receiver_nonce":str(uuid.uuid4()),
                      "query_ref":f"synthetic-request-{i}"}
            return witness.endorse(**proposed)["status"]
        with ThreadPoolExecutor(max_workers=n) as pool:
            statuses=list(pool.map(attempt,range(n)))
        assert statuses.count("witnessed-not-executable")==1
        assert witness.inspect()["occupied"]==1
    finally:cleanup(folder)


def test_key_never_leaves_witness_directory_during_proposals():
    folder,epoch,enrolled,witness,args=fixture()
    try:
        assert os.stat(Path(folder)/"witness-test.key").st_mode & 0o077 == 0
        assert os.stat(Path(folder)/"witness-test.pub").st_mode & 0o077 == 0
        result=witness.endorse(**args)
        assert "key" not in result
        assert len(result["signature_hex"])==128
    finally:cleanup(folder)


def test_signer_identity_survives_restart_without_issuing_second_token():
    folder,epoch,enrolled,witness,args=fixture()
    try:
        assert witness.endorse(**args)["status"]=="witnessed-not-executable"
        reopened=VetoWitnessResearch(folder,pinned_epoch=epoch,
            pinned_graph_id=GRAPH,pinned_signer_digest=enrolled["signer_sha256"],
            independently_pinned_minimum_sequence=1)
        assert reopened.inspect()==witness.inspect()
        another={**args,"token":"c"*32,"receiver_nonce":str(uuid.uuid4()),
                 "sequence":2,"query_ref":"second"}
        assert reopened.endorse(**another)["status"]=="witness-capacity-full"
    finally:cleanup(folder)


def test_missing_or_replaced_witness_denies_no_autobootstrap():
    folder,epoch,enrolled,witness,args=fixture()
    try:
        p=Path(folder)/"witness-test.sqlite"
        p.unlink()
        with pytest.raises((OSError,ValueError)):
            VetoWitnessResearch(folder,pinned_epoch=epoch,pinned_graph_id=GRAPH,
                pinned_signer_digest=enrolled["signer_sha256"])
        assert witness.endorse(**args)["status"]=="witness-unavailable"
    finally:cleanup(folder)


def test_external_high_water_rejects_rolled_back_witness():
    folder,epoch,enrolled,witness,args=fixture()
    try:
        path=Path(folder)/"witness-test.sqlite"
        before=Path(folder)/"previous.snapshot"
        shutil.copyfile(path,before)
        assert witness.endorse(**args)["status"]=="witnessed-not-executable"
        shutil.copyfile(before,path)
        with pytest.raises(ValueError,match="CHECKPOINT"):
            VetoWitnessResearch(folder,pinned_epoch=epoch,pinned_graph_id=GRAPH,
                pinned_signer_digest=enrolled["signer_sha256"],
                independently_pinned_minimum_sequence=1)
        # Stale checkpoint 0 is unsafe; this must remain an expected negative.
        new=VetoWitnessResearch(folder,pinned_epoch=epoch,pinned_graph_id=GRAPH,
            pinned_signer_digest=enrolled["signer_sha256"],
            independently_pinned_minimum_sequence=0)
        assert new.endorse(**args)["status"]=="witnessed-not-executable"
        before.unlink()
    finally:cleanup(folder)


def test_copied_private_witness_can_fork_global_grants_negative_control():
    folder,epoch,enrolled,witness,args=fixture()
    other=tempfile.TemporaryDirectory(prefix="assistx-twohost-witness-test-",dir="/tmp")
    try:
        for name in ("witness-test.key","witness-test.pub","witness-test.sqlite"):
            shutil.copyfile(Path(folder)/name,Path(other.name)/name)
            (Path(other.name)/name).chmod(0o600)
        replica=VetoWitnessResearch(other.name,pinned_epoch=epoch,
            pinned_graph_id=GRAPH,pinned_signer_digest=enrolled["signer_sha256"])
        left=witness.endorse(**args)
        right=replica.endorse(token="c"*32,receiver_nonce=str(uuid.uuid4()),
                             query_ref="independent-fork",sequence=1)
        assert left["status"]=="witnessed-not-executable"
        assert right["status"]=="witnessed-not-executable"
        assert witness.inspect()["occupied"]==1 and replica.inspect()["occupied"]==1
    finally:
        cleanup(folder)
        cleanup(other.name)
        other.cleanup()


def test_wrong_key_digest_cannot_be_substituted_by_receipt():
    folder,epoch,enrolled,witness,args=fixture()
    try:
        with pytest.raises(ValueError,match="RESEARCH_KEY_DIGEST_NOT_PINNED"):
            VetoWitnessResearch(folder,pinned_epoch=epoch,pinned_graph_id=GRAPH,
                pinned_signer_digest="f"*64)
        assert not witness.endorse(token="not-a-token",receiver_nonce=args["receiver_nonce"],
                                   query_ref=args["query_ref"],sequence=1)["status"].startswith("witnessed")
    finally:cleanup(folder)
