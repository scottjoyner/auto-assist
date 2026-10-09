"""Isolated third-node witness tests: single-owner success, stale-copy NO-GO."""
from concurrent.futures import ProcessPoolExecutor,ThreadPoolExecutor
from pathlib import Path
import importlib.util
import multiprocessing
import os
import shutil
import tempfile
import uuid
import pytest

file=Path(__file__).with_name("probe_trace_third_node_witness.py")
spec=importlib.util.spec_from_file_location("physical_third_witness_research",file)
import sys
witness=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=witness
spec.loader.exec_module(witness)


def make():
    tmp=tempfile.TemporaryDirectory(prefix="assistx-threehost-witness-test-",dir="/tmp")
    name=str(Path(tmp.name)/"witness-test.sqlite")
    epoch=str(uuid.uuid4())
    graph="a"*64
    witness.bootstrap(name,epoch,graph)
    return tmp,name,epoch,graph


def contender(args):
    path,epoch,graph,number=args
    return witness.Witness(path,epoch,graph).request("synthetic-process-"+str(number))["status"]


@pytest.mark.parametrize("n",[1,3,5,10])
def test_one_surviving_central_witness_enforces_capacity(n):
    tmp,path,epoch,graph=make()
    try:
        with ThreadPoolExecutor(max_workers=n) as pool:
            decisions=list(pool.map(lambda i:witness.Witness(path,epoch,graph).request(
                "synthetic-request-"+str(i))["status"],range(n)))
        assert decisions.count("admitted")==1
        assert decisions.count("full")==n-1
        assert witness.Witness(path,epoch,graph).status()["active"]==1
    finally:tmp.cleanup()


def test_separate_processes_share_one_witness_only():
    tmp,path,epoch,graph=make()
    try:
        with ProcessPoolExecutor(max_workers=4,
            mp_context=multiprocessing.get_context("spawn")) as pool:
            statuses=list(pool.map(contender,[(path,epoch,graph,i) for i in range(4)]))
        assert statuses.count("admitted")==1 and statuses.count("full")==3
    finally:tmp.cleanup()


def test_surviving_witness_denies_after_client_restart():
    tmp,path,epoch,graph=make()
    try:
        first=witness.Witness(path,epoch,graph).request("synthetic-first")
        assert first["status"]=="admitted" and first["sequence"]==1
        second=witness.Witness(path,epoch,graph).request("synthetic-after-restart")
        assert second["status"]=="full" and second["sequence"]==1
    finally:tmp.cleanup()


def test_wrong_epoch_missing_state_and_owner_copy_are_refused():
    tmp,path,epoch,graph=make()
    try:
        with pytest.raises(ValueError):
            witness.Witness(path,str(uuid.uuid4()),graph)
        with pytest.raises(ValueError):
            witness.Witness(path,epoch,"b"*64)
        Path(path).unlink()
        with pytest.raises((OSError,ValueError)):
            witness.Witness(path,epoch,graph)
        assert not Path(path).exists()
    finally:tmp.cleanup()


def test_two_copies_of_same_witness_can_each_grant_expected_counterexample():
    tmp,path,epoch,graph=make()
    with tempfile.TemporaryDirectory(prefix="assistx-threehost-witness-test-",dir="/tmp") as second:
        try:
            clone=str(Path(second)/"witness-test.sqlite")
            shutil.copyfile(path,clone)
            Path(clone).chmod(0o600)
            assert witness.Witness(path,epoch,graph).request("synthetic-x1")["status"]=="admitted"
            assert witness.Witness(clone,epoch,graph).request("synthetic-xwing")["status"]=="admitted"
        finally:tmp.cleanup()


def test_unsafe_paths_and_rebootstrap_fail_closed():
    tmp,path,epoch,graph=make()
    try:
        with pytest.raises(ValueError):
            witness.bootstrap(path,epoch,graph)
        with pytest.raises(ValueError):
            witness.bootstrap("/nas/witness-test.sqlite",epoch,graph)
        with pytest.raises(ValueError):
            witness.bootstrap("/tmp/not-research/witness-test.sqlite",epoch,graph)
    finally:tmp.cleanup()


def test_no_release_api_exposed():
    assert not hasattr(witness.Witness,"release")
    assert not hasattr(witness.Witness,"terminate")
    assert not hasattr(witness.Witness,"rearm")


def test_rewinding_witness_journal_reissues_sequence_one_negative_control():
    """Expected failure: independent third-host SQLite is still rollbackable."""
    tmp,path,epoch,graph=make()
    try:
        checkpoint=Path(tmp.name)/"empty.snapshot"
        shutil.copyfile(path,checkpoint)
        first=witness.Witness(path,epoch,graph).request("synthetic-before-rollback")
        assert first["status"]=="admitted" and first["sequence"]==1
        inode=Path(path).stat().st_ino
        # Replacing CONTENT in place defeats simple inode checks.
        shutil.copyfile(checkpoint,path)
        assert Path(path).stat().st_ino==inode
        second=witness.Witness(path,epoch,graph).request("synthetic-after-rollback")
        assert second["status"]=="admitted" and second["sequence"]==1
        assert first["token"]!=second["token"]
        assert witness.Witness(path,epoch,graph).status()["active"]==1
    finally:tmp.cleanup()
