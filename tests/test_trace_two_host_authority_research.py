"""#148 single-authority guardrails + expected split-brain counterexamples.

These use only isolated /tmp files; they prove a surviving central SQLite
owner serializes competing callers, NOT that filesystem copies are safe.
"""
from concurrent.futures import ThreadPoolExecutor,ProcessPoolExecutor
from pathlib import Path
import multiprocessing as mp
import os
import shutil
import tempfile
import uuid

import pytest
from assistx.trace_two_host_authority_research import (
    SingleAuthorityResearch,bootstrap_research
)

GRAPH="a"*64


def fixture(capacity=1):
    temp=tempfile.TemporaryDirectory(prefix="assistx-twohost-authority-test-",dir="/tmp")
    path=str(Path(temp.name)/"authority-test.sqlite")
    epoch=str(uuid.uuid4())
    bootstrap_research(path,epoch=epoch,graph_id=GRAPH,capacity=capacity)
    return temp,path,epoch


def owner(path,epoch,floor=0,graph=GRAPH):
    return SingleAuthorityResearch(path,pinned_epoch=epoch,
                                   pinned_graph_id=graph,minimum_sequence=floor)


def process_acquire(inputs):
    path,epoch,ref=inputs
    return owner(path,epoch).admit(ref).status


@pytest.mark.parametrize("n",[1,3,5,10])
def test_concurrent_wave_never_exceeds_central_capacity(n):
    tmp,path,epoch=fixture()
    try:
        with ThreadPoolExecutor(max_workers=n) as pool:
            results=list(pool.map(lambda i:owner(path,epoch).admit(f"wave-{i}").status,range(n)))
        assert results.count("admitted")==1
        assert results.count("full")==n-1
        assert owner(path,epoch).snapshot()["active"]==1
    finally:tmp.cleanup()


def test_separate_spawned_processes_share_one_durable_authority():
    tmp,path,epoch=fixture()
    try:
        tasks=[(path,epoch,f"process-{i}") for i in range(5)]
        with ProcessPoolExecutor(max_workers=5,mp_context=mp.get_context("spawn")) as p:
            results=list(p.map(process_acquire,tasks))
        assert results.count("admitted")==1
        assert results.count("full")==4
        assert owner(path,epoch).snapshot()["sequence"]==1
    finally:tmp.cleanup()


def test_remote_worker_restart_does_not_rearm_capacity():
    tmp,path,epoch=fixture()
    try:
        initial=owner(path,epoch).admit("first-physical-query")
        assert initial.status=="admitted" and initial.receiver_nonce
        assert owner(path,epoch,1).admit("restarted-request").status=="full"
        assert owner(path,epoch).snapshot()["active"]==1
    finally:tmp.cleanup()


@pytest.mark.parametrize("failure",["missing","graph","epoch","future-checkpoint"])
def test_authority_uncertainty_refuses_new_admission(failure):
    tmp,path,epoch=fixture()
    try:
        kwargs={"pinned_epoch":epoch,"pinned_graph_id":GRAPH,"minimum_sequence":0}
        if failure=="missing":Path(path).unlink()
        if failure=="graph":kwargs["pinned_graph_id"]="b"*64
        if failure=="epoch":kwargs["pinned_epoch"]=str(uuid.uuid4())
        if failure=="future-checkpoint":kwargs["minimum_sequence"]=1
        with pytest.raises((ValueError,OSError)):
            SingleAuthorityResearch(path,**kwargs)
    finally:tmp.cleanup()


def test_disposable_owner_rejects_nonfixture_path_and_symlink(tmp_path):
    with pytest.raises(ValueError,match="DISPOSABLE_TWOHOST_AUTHORITY_PATH_REQUIRED"):
        bootstrap_research("/nas/authority-test.sqlite",epoch=str(uuid.uuid4()),graph_id=GRAPH)
    tmp,path,epoch=fixture()
    try:
        link=tmp_path/"authority-test.sqlite"
        link.symlink_to(path)
        with pytest.raises(ValueError):
            owner(str(link),epoch)
    finally:tmp.cleanup()


def test_readonly_observe_never_grants_or_releases_capacity():
    tmp,path,epoch=fixture()
    try:
        assert owner(path,epoch).snapshot()["active"]==0
        d=owner(path,epoch).admit("first")
        assert d.status=="admitted" and d.sequence==1
        assert owner(path,epoch).snapshot()["active"]==1
        assert owner(path,epoch,1).admit("second").status=="full"
    finally:tmp.cleanup()


def test_two_independent_copies_each_admit_global_capacity_negative_control():
    # Expected counterexample! Matching epoch and schema on two different
    # machines does NOT give the copies mutual exclusion.
    first,p1,epoch=fixture()
    second=tempfile.TemporaryDirectory(prefix="assistx-twohost-authority-test-",dir="/tmp")
    p2=str(Path(second.name)/"authority-test.sqlite")
    try:
        shutil.copyfile(p1,p2)
        Path(p2).chmod(0o600)
        one=owner(p1,epoch).admit("request-from-x1")
        two=owner(p2,epoch).admit("request-from-xwing")
        assert one.status=="admitted"
        assert two.status=="admitted"
        assert one.token!=two.token
    finally:
        first.cleanup();second.cleanup()


def test_external_high_water_detects_snapshot_rollback_but_stale_floor_does_not():
    tmp,path,epoch=fixture()
    snapshot=Path(tmp.name)/"old-copy"
    try:
        shutil.copyfile(path,snapshot)
        first=owner(path,epoch).admit("first")
        assert first.sequence==1
        # Deliberately restore bytes into SAME path, preserving inode.
        shutil.copyfile(snapshot,path)
        with pytest.raises(ValueError,match="OWNER_EPOCH_GRAPH_OR_CHECKPOINT_UNTRUSTED"):
            owner(path,epoch,1)
        # Expected unsafe counterexample if trusted floor is stale/zero.
        assert owner(path,epoch,0).admit("stale-client").status=="admitted"
    finally:tmp.cleanup()


def test_unavailable_owner_never_creates_new_journal():
    tmp,path,epoch=fixture()
    try:
        Path(path).unlink()
        with pytest.raises((OSError,ValueError)):
            owner(path,epoch)
        assert not Path(path).exists()
    finally:tmp.cleanup()
