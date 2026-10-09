"""Isolated witness is a single new authority, never failover or quorum."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import tempfile
import uuid

import pytest

from assistx.trace_two_host_authority_research import (
    SingleAuthorityResearch, bootstrap_research
)
from assistx.trace_two_host_witness_research import (
    Witness, bootstrap
)

GRAPH = "a" * 64


def fixture():
    temporary = tempfile.TemporaryDirectory(
        prefix="assistx-twohost-witness-research-", dir="/tmp")
    path = str(Path(temporary.name) / "witness-test.sqlite")
    epoch = str(uuid.uuid4())
    bootstrap(path, epoch=epoch, graph=GRAPH)
    return temporary, path, epoch


def witness(path, epoch, floor=1):
    return Witness(path, epoch=epoch, graph=GRAPH, floor=floor)


@pytest.mark.parametrize("requests", [1, 3, 5, 10])
def test_one_witness_serializes_competing_local_requests(requests):
    temp, path, epoch = fixture()
    try:
        with ThreadPoolExecutor(max_workers=requests) as pool:
            decisions = list(pool.map(
                lambda n: witness(path, epoch).admit(
                    f"synthetic-{n}", "x1-370", 1),
                range(requests)))
        assert sum(x.status == "admitted" for x in decisions) == 1
        assert sum(x.status == "capacity-full" for x in decisions) == requests - 1
        assert witness(path, epoch).snapshot()["active"] == 1
    finally:
        temp.cleanup()


def test_copied_sqlite_owner_grants_do_not_become_fenced_effects():
    temp, path, epoch = fixture()
    a = tempfile.TemporaryDirectory(prefix="assistx-twohost-authority-test-", dir="/tmp")
    b = tempfile.TemporaryDirectory(prefix="assistx-twohost-authority-test-", dir="/tmp")
    pa, pb = str(Path(a.name) / "authority-test.sqlite"), str(Path(b.name) / "authority-test.sqlite")
    try:
        bootstrap_research(pa, epoch=epoch, graph_id=GRAPH)
        shutil.copyfile(pa, pb)
        Path(pb).chmod(0o600)
        local_a = SingleAuthorityResearch(pa, pinned_epoch=epoch,
                                           pinned_graph_id=GRAPH, minimum_sequence=0)
        local_b = SingleAuthorityResearch(pb, pinned_epoch=epoch,
                                           pinned_graph_id=GRAPH, minimum_sequence=0)
        assert local_a.admit("local-a").status == "admitted"
        assert local_b.admit("local-b").status == "admitted"
        # Independent journals still issued two grants: a known failure.
        gate = witness(path, epoch)
        grant = gate.admit("synthetic-a", "x1-370", 1)
        assert grant.status == "admitted"
        assert gate.admit("synthetic-b", "x1-370", 1).status == "capacity-full"
        assert gate.admit("synthetic-xwing", "xwing", 1).status == "wrong-holder"
        assert gate.synthetic_effect(grant.token, "x1-370", 1,
                                     "synthetic-effect-a").status == "applied-synthetic"
        assert gate.synthetic_effect("forged-token", "x1-370", 1,
                                     "synthetic-effect-b").status == "unadmitted-effect"
        assert gate.snapshot()["active"] == 1
    finally:
        a.cleanup()
        b.cleanup()
        temp.cleanup()


def test_rotation_refuses_active_and_fences_old_owner_and_replayed_release():
    temp, path, epoch = fixture()
    try:
        gate = witness(path, epoch)
        prior = gate.admit("synthetic-original", "x1-370", 1)
        assert prior.status == "admitted"
        assert gate.rotate("xwing", 1).status == "reconciliation-required"
        assert gate.complete(prior.token, "x1-370", 1).status == "completed"
        assert gate.rotate("xwing", 1).status == "rotated"
        assert gate.snapshot()["term"] == 2
        assert gate.admit("synthetic-old", "x1-370", 1).status == "stale-term"
        successor = gate.admit("synthetic-new", "xwing", 2)
        assert successor.status == "admitted"
        assert gate.complete(prior.token, "x1-370", 1).status == "stale-completion"
        assert gate.synthetic_effect(prior.token, "x1-370", 1,
                                     "synthetic-old-effect").status == "stale-effect"
        assert gate.synthetic_effect(successor.token, "xwing", 2,
                                     "synthetic-new-effect").status == "applied-synthetic"
        assert gate.synthetic_effect(successor.token, "xwing", 2,
                                     "synthetic-new-effect").status == "duplicate-effect"
        assert gate.snapshot()["active"] == 1
    finally:
        temp.cleanup()


def test_missing_witness_denies_no_local_fallback():
    temp, path, epoch = fixture()
    try:
        Path(path).unlink()
        with pytest.raises((OSError, ValueError)):
            witness(path, epoch)
        assert not Path(path).exists()
    finally:
        temp.cleanup()


def test_external_term_floor_detects_same_inode_snapshot_rollback():
    temp, path, epoch = fixture()
    old = Path(temp.name) / "pre-rotation.snapshot"
    try:
        shutil.copyfile(path, old)
        assert witness(path, epoch).rotate("xwing", 1).term == 2
        shutil.copyfile(old, path)
        with pytest.raises(ValueError, match="WITNESS_IDENTITY_OR_TERM_ROLLBACK"):
            witness(path, epoch, floor=2)
        # Counterexample: a *stale* caller floor accepts the rollback.
        assert witness(path, epoch, floor=1).snapshot()["term"] == 1
    finally:
        temp.cleanup()


def test_cloning_the_witness_itself_is_unsafe_not_failover():
    temp, path, epoch = fixture()
    other = tempfile.TemporaryDirectory(
        prefix="assistx-twohost-witness-research-", dir="/tmp")
    copied = str(Path(other.name) / "witness-test.sqlite")
    try:
        shutil.copyfile(path, copied)
        Path(copied).chmod(0o600)
        assert witness(path, epoch).admit("synthetic-a", "x1-370", 1).status == "admitted"
        assert witness(copied, epoch).admit("synthetic-b", "x1-370", 1).status == "admitted"
    finally:
        other.cleanup()
        temp.cleanup()

def test_spoofable_holder_is_an_explicit_security_blocker():
    temp, path, epoch = fixture()
    try:
        gate = witness(path, epoch)
        assert gate.rotate("xwing", 1).status == "rotated"
        # There is no real caller authentication in this contract fixture.
        # A process impersonating xwing can supply the current term.
        spoofed = gate.admit("synthetic-impersonated", "xwing", 2)
        assert spoofed.status == "admitted"
        assert gate.synthetic_effect(spoofed.token, "xwing", 2,
                                     "synthetic-impersonated-effect").status == "applied-synthetic"
    finally:
        temp.cleanup()
